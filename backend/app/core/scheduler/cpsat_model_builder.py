"""CP-SAT Model Builder — segment-based scheduling (v2).

Phase 2 of the scheduling pipeline.  Each operation is **decomposed into work
segments**, one per candidate operator shift-slot.  The segments of a single
operation may belong to *different operators* and *different shifts*, so a long
operation can be started by one operator in one shift and finished by another
operator in a later shift (calendar-preemption / hand-off).

Key consequences vs. the old v1 model:
  * The operator shift calendar is a **hard** CP-SAT constraint — segments live
    strictly inside their slot — so no post-solve "forward pass" correction is
    needed and `op_end` is the exact wall-clock end (makespan / FINISH_BY_DATE
    are exact, not estimates).
  * An operation is no longer tied to a single operator: work is allocated as
    `Σ segment.size == residual_duration`.
  * Two no-overlap families guarantee physical feasibility:
      - per operator  → an operator does one thing at a time;
      - per operation → an operation is worked by one operator at a time
                        (sequential hand-off, no phantom parallelism).

Pipeline:
  _create_variables                 — segments, op_start/op_end, assignments
  _add_resource_nooverlap_constraints — per-operator + per-operation NoOverlap
  _add_precedence_constraints       — end(pred) ≤ start(succ); RP blocking
  _add_rp_order_constraints         — Reference-Point DAG ordering
  _add_parent_wait_constraints      — BOM parent waits for children
  _add_missing_component_constraints — start ≥ component_arrival_minute
  _set_objective                    — FINISH_BY_DATE / MINIMIZE_OPERATORS /
                                      MAXIMIZE_RESOURCE_UTILIZATION / CUSTOM
  build_and_solve                   — orchestrates, returns CpsatSolution
"""
from __future__ import annotations

from asyncio.log import logger
import math
import os
import uuid
from collections import defaultdict, deque
from datetime import datetime

from ortools.sat.python import cp_model

from app.core.scheduler.cpsat_types import (
    CpsatSolution,
    CpsatVariables,
    QualifiedOperator,
    SchedulableOperation,
    SegmentVars,
    operator_can_do,
)


class CpsatModelBuilder:
    """Builds and solves a segment-based CP-SAT scheduling model."""

    MIN_OP_DURATION: int = int(os.getenv("MIN_OP_DURATION_MINUTES", "30"))
    TIMEOUT: float = float(os.getenv("CPSAT_TIMEOUT_SECONDS", "30"))
    # Per operation we generate only the nearest slots whose cumulated capacity
    # reaches FACTOR × residual_duration.  Keeps the model small while leaving
    # slack for calendar gaps and operator contention.  Raise it if the solver
    # returns spurious INFEASIBLE on heavily-loaded instances.
    SLOT_CAPACITY_FACTOR: float = float(os.getenv("CPSAT_SLOT_CAPACITY_FACTOR", "4.0"))

    def __init__(
        self,
        operations: list[SchedulableOperation],
        operators: list[QualifiedOperator],
        horizon_minutes: int,
        epoch: datetime,
        missing_components_constraints: dict[uuid.UUID, int],
        precedence_pairs: list[tuple[uuid.UUID, uuid.UUID]],
    ) -> None:
        self.model = cp_model.CpModel()
        self.solver = cp_model.CpSolver()

        self.operations = operations
        self.operators = operators
        self.horizon = horizon_minutes
        self.epoch = epoch
        self.missing_constraints = missing_components_constraints
        self.precedence_pairs = precedence_pairs
        self.rp_order_constraints: list[tuple[list[uuid.UUID], list[uuid.UUID]]] = []
        self.parent_wait_constraints: list[tuple[list[uuid.UUID], uuid.UUID]] = []

        self.vars: CpsatVariables | None = None
        self._infeasibility_reasons: list[str] = []
        self._blocking_constraints: dict[uuid.UUID, int] = {}

        # Slot-pruning factor for the current solve attempt (None = keep all slots).
        self._slot_cap_factor: float | None = self.SLOT_CAPACITY_FACTOR
        self._union_slots_cache: dict[tuple, list[tuple[int, int]]] = {}

        self._op_index: dict[uuid.UUID, SchedulableOperation] = {
            op.id: op for op in operations
        }

    # ── Pure helpers ──────────────────────────────────────────────────────────

    def _compute_residual_duration(self, op: SchedulableOperation) -> int:
        """Remaining work = planned × (1 - progress/100), clamped to MIN_OP_DURATION."""
        residual = op.planned_duration_minutes * (1.0 - op.progress_pct / 100.0)
        return max(int(math.ceil(residual)), self.MIN_OP_DURATION)

    def _get_qualified_operators(self, op: SchedulableOperation) -> list[QualifiedOperator]:
        return [
            oper for oper in self.operators
            if oper.workcenter_id == op.workcenter_id
            and operator_can_do(oper, op.operation_type)
            and len(oper.available_slots) > 0
        ]

    @staticmethod
    def _merge_intervals(slots: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Merge overlapping/adjacent (start, end) intervals into a disjoint sorted list."""
        if not slots:
            return []
        merged: list[tuple[int, int]] = []
        for s, e in sorted(slots):
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        return merged

    @staticmethod
    def _slot_aware_end(merged_slots: list[tuple[int, int]], earliest_start: int, duration: int) -> int:
        """Wall-clock minute at which `duration` work-minutes finish, consuming the
        availability windows `merged_slots` (disjoint, sorted) from `earliest_start`.

        Optimistic lower bound on the real completion: ignores resource contention
        (assumes any qualified operator's slot is usable) but *does* account for
        calendar gaps (shift ends, breaks, days off). One operator at a time, so the
        union must be merged (overlaps don't add capacity).
        """
        if duration <= 0:
            return earliest_start
        remaining = duration
        cursor = earliest_start
        for s, e in merged_slots:
            if e <= cursor:
                continue
            work_from = max(s, cursor)
            avail = e - work_from
            if avail >= remaining:
                return work_from + remaining
            remaining -= avail
            cursor = e
        # Not enough calendar before the horizon — extend linearly past the last slot.
        return cursor + remaining

    def _qualified_union_slots(self, op: SchedulableOperation) -> list[tuple[int, int]]:
        """Merged availability of all operators qualified for `op` (cached per WC+type)."""
        key = (op.workcenter_id, op.operation_type)
        cached = self._union_slots_cache.get(key)
        if cached is None:
            raw: list[tuple[int, int]] = []
            for oper in self._get_qualified_operators(op):
                raw.extend(oper.available_slots)
            cached = self._merge_intervals(raw)
            self._union_slots_cache[key] = cached
        return cached

    def _compute_est(self) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
        """Calendar-aware earliest-start lower bound per op (longest path on the DAG).

        Returns ``(est_start, est_end)``.

        Unlike a pure work-time estimate, this accounts for calendar gaps: a long
        op spread over sparse shifts finishes far in wall-clock, which pushes its
        successors' windows to the *late* slots where they actually belong. This is
        what keeps candidate-slot pruning sound (work-time est anchored deep parent
        ops to early slots → false INFEASIBLE).

        est_start[op] = max(earliest, arrival, max preds est_end[pred])
        est_end[op]   = slot_aware_end(union_slots(op), est_start[op], D[op])
        Both are *lower* bounds (optimistic: ignore contention).
        """
        ids = set(self._op_index)
        preds: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)

        for pred_id, succ_id in self.precedence_pairs:
            if pred_id in ids and succ_id in ids:
                preds[succ_id].add(pred_id)
        for ops_pred, ops_succ in self.rp_order_constraints:
            for succ_id in (s for s in ops_succ if s in ids):
                for pred_id in (p for p in ops_pred if p in ids):
                    preds[succ_id].add(pred_id)
        for ops_target, parent_op_id in self.parent_wait_constraints:
            if parent_op_id in ids:
                for target_id in (t for t in ops_target if t in ids):
                    preds[parent_op_id].add(target_id)

        indeg = {oid: len(preds.get(oid, ())) for oid in ids}
        succ: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for oid in ids:
            for pred_id in preds.get(oid, ()):
                succ[pred_id].append(oid)

        est_start: dict[uuid.UUID, int] = {}
        est_end: dict[uuid.UUID, int] = {}

        def finalize(oid: uuid.UUID) -> None:
            op = self._op_index[oid]
            base = max(op.earliest_start_minutes, self.missing_constraints.get(oid, 0))
            pred_end = max((est_end[p] for p in preds.get(oid, ()) if p in est_end), default=0)
            s = max(base, pred_end)
            est_start[oid] = s
            est_end[oid] = self._slot_aware_end(
                self._qualified_union_slots(op), s, self._compute_residual_duration(op)
            )

        q: deque[uuid.UUID] = deque(oid for oid in ids if indeg[oid] == 0)
        seen = 0
        while q:
            cur = q.popleft()
            finalize(cur)
            seen += 1
            for s in succ[cur]:
                indeg[s] -= 1
                if indeg[s] == 0:
                    q.append(s)
        if seen != len(ids):  # cycle → finalize the rest with whatever preds resolved
            logger.warning("EST: dependency cycle detected for %d ops, using partial bounds", len(ids) - seen)
            for oid in ids:
                if oid not in est_start:
                    finalize(oid)
        return est_start, est_end

    # Margine di sicurezza sull'orizzonte auto-dimensionato: il LB del makespan è
    # ottimistico (ignora la contesa), quindi diamo spazio (×N) perché il solver
    # impacchetti senza diventare INFEASIBLE. Cap finale sempre alla fine calendario.
    HORIZON_SAFETY: float = float(os.getenv("CPSAT_HORIZON_SAFETY", "1.5"))

    def _autosize_horizon(self) -> None:
        """Restringe l'orizzonte CP-SAT al *carico*, scollegandolo dalla lunghezza
        del calendario operatori.

        Il calendario serve solo a fornire gli slot di disponibilità; l'orizzonte
        invece dimensiona il dominio delle variabili (op_start/op_end/makespan ∈
        [0, horizon]). Se lo si lega alla fine del calendario, un calendario lungo
        (mesi/anni) gonfia ogni dominio e rende intrattabile la ricerca
        NoOverlap/makespan, pur esistendo una soluzione nei primi giorni.

        Usiamo il lower-bound calendario-aware del makespan (max est_end) × margine,
        limitato dall'orizzonte originale (fine calendario). Così il calendario può
        essere lungo a piacere senza penalizzare il solver.
        """
        import logging
        _log = logging.getLogger(__name__)

        _est_start, est_end = self._compute_est()
        makespan_lb = max(est_end.values(), default=0)
        if makespan_lb <= 0:
            return

        tight = int(makespan_lb * self.HORIZON_SAFETY)
        new_horizon = min(self.horizon, max(tight, makespan_lb))
        if new_horizon < self.horizon:
            _log.info(
                "Auto-horizon: makespan LB=%d min → orizzonte %d→%d min "
                "(margine ×%.1f, calendario scollegato dai domini)",
                makespan_lb, self.horizon, new_horizon, self.HORIZON_SAFETY,
            )
            self.horizon = new_horizon

    def _candidate_slots(
        self, op: SchedulableOperation, est_op: int,
    ) -> tuple[list[tuple[uuid.UUID, int, int, int]], int]:
        """Return (candidate (operator_id, slot_start, slot_end, usable_lb) list, total_capacity).

        A slot is a candidate if the operator is qualified and the slot has usable
        room after the op's earliest feasible minute (`est_op`, calendar-aware).

        If ``self._slot_cap_factor`` is set, keep only the nearest slots whose
        cumulated capacity reaches FACTOR × residual; if it is ``None`` keep them all
        (used by the no-cap retry that rules out a pruning-induced false INFEASIBLE).
        """
        lb0 = max(op.earliest_start_minutes, self.missing_constraints.get(op.id, 0), est_op)

        cands: list[tuple[uuid.UUID, int, int, int]] = []
        for oper in self._get_qualified_operators(op):
            for slot_s, slot_e in oper.available_slots:
                lb = max(slot_s, lb0)
                if slot_e - lb >= 1:
                    cands.append((oper.id, slot_s, slot_e, lb))

        cands.sort(key=lambda c: c[3])  # by usable start time

        if self._slot_cap_factor is None:
            return cands, sum(c[2] - c[3] for c in cands)

        D = self._compute_residual_duration(op)
        cap_target = D * self._slot_cap_factor
        kept: list[tuple[uuid.UUID, int, int, int]] = []
        acc = 0
        for c in cands:
            kept.append(c)
            acc += c[2] - c[3]
            if acc >= cap_target:
                break
        return kept, acc

    # ── Variable creation ──────────────────────────────────────────────────────

    def _create_variables(self) -> CpsatVariables:
        model = self.model
        est, _est_end = self._compute_est()

        op_start: dict[uuid.UUID, cp_model.IntVar] = {}
        op_end: dict[uuid.UUID, cp_model.IntVar] = {}
        op_duration: dict[uuid.UUID, int] = {}
        assignments: dict[tuple[uuid.UUID, uuid.UUID], cp_model.BoolVar] = {}
        segments: dict[uuid.UUID, list[SegmentVars]] = {}

        for op in self.operations:
            D = self._compute_residual_duration(op)
            op_duration[op.id] = D

            est_op = est.get(op.id, 0)
            cands, total_cap = self._candidate_slots(op, est_op)
            if not cands:
                # No qualified operator with available capacity *after est_op* → the
                # operation cannot be scheduled.  A schedule that silently drops a
                # required operation is invalid, so the whole model is infeasible.
                qualified = self._get_qualified_operators(op)
                last_slot_end = max(
                    (e for oper in qualified for _, e in oper.available_slots),
                    default=0,
                )
                if qualified and est_op >= last_slot_end:
                    # The operator calendar *does* cover this op, but its earliest
                    # feasible start (from precedence/missing-component chains) lands
                    # past the end of the calendar/horizon.  This is the real cause,
                    # not a missing operator — surface it explicitly.
                    reason = (
                        f"Op {op.id} ({op.operation_type.value}): earliest-start "
                        f"est={est_op} min oltre fine calendario operatori "
                        f"({last_slot_end} min) — catena di precedenze/RP/parent-wait "
                        f"o componenti mancanti troppo lunga per l'orizzonte"
                    )
                else:
                    reason = (
                        f"Op {op.id} ({op.operation_type.value}): nessuno slot operatore "
                        f"disponibile (workcenter/skill/calendario)"
                    )
                logger.warning("INFEASIBLE op: %s", reason)
                self._infeasibility_reasons.append(reason)
                contradiction = model.NewIntVar(0, 0, f"unsched_{op.id}")
                model.Add(contradiction == 1)
                continue
            if total_cap < D:
                self._infeasibility_reasons.append(
                    f"Op {op.id}: capacità operatori insufficiente "
                    f"({total_cap} < {D} min) entro l'orizzonte"
                )

            seg_list: list[SegmentVars] = []
            per_oper_pres: dict[uuid.UUID, list[cp_model.BoolVar]] = defaultdict(list)
            eff_starts: list[cp_model.IntVar] = []
            eff_ends: list[cp_model.IntVar] = []

            for idx, (oper_id, slot_s, slot_e, lb) in enumerate(cands):
                cap = slot_e - lb
                tag = f"{op.id}_{oper_id}_{idx}"

                pres = model.NewBoolVar(f"pres_{tag}")
                size = model.NewIntVar(0, cap, f"size_{tag}")
                start = model.NewIntVar(lb, slot_e, f"ss_{tag}")
                end = model.NewIntVar(lb, slot_e, f"se_{tag}")
                iv = model.NewOptionalIntervalVar(start, size, end, pres, f"seg_{tag}")

                # size ⟺ present
                model.Add(size == 0).OnlyEnforceIf(pres.Not())
                model.Add(size >= 1).OnlyEnforceIf(pres)

                # effective start/end for exact min/max (sentinel when absent)
                eff_s = model.NewIntVar(0, self.horizon, f"effs_{tag}")
                model.Add(eff_s == start).OnlyEnforceIf(pres)
                model.Add(eff_s == self.horizon).OnlyEnforceIf(pres.Not())
                eff_e = model.NewIntVar(0, self.horizon, f"effe_{tag}")
                model.Add(eff_e == end).OnlyEnforceIf(pres)
                model.Add(eff_e == 0).OnlyEnforceIf(pres.Not())
                eff_starts.append(eff_s)
                eff_ends.append(eff_e)

                seg_list.append(
                    SegmentVars(
                        operator_id=oper_id, slot_start=slot_s, slot_end=slot_e,
                        present=pres, start=start, size=size, end=end, interval=iv,
                    )
                )
                per_oper_pres[oper_id].append(pres)

            # All residual work must be allocated across the segments.
            model.Add(sum(sv.size for sv in seg_list) == D)

            # Aggregate op_start (first present start) / op_end (last present end).
            os_var = model.NewIntVar(0, self.horizon, f"opstart_{op.id}")
            oe_var = model.NewIntVar(0, self.horizon, f"opend_{op.id}")
            model.AddMinEquality(os_var, eff_starts)
            model.AddMaxEquality(oe_var, eff_ends)
            op_start[op.id] = os_var
            op_end[op.id] = oe_var
            segments[op.id] = seg_list

            # Reified "operator o works part of op" = OR of its segment-present vars.
            for oper_id, pres_list in per_oper_pres.items():
                a = model.NewBoolVar(f"assign_{op.id}_{oper_id}")
                model.AddMaxEquality(a, pres_list)
                assignments[(op.id, oper_id)] = a

        return CpsatVariables(
            op_start=op_start, op_end=op_end, op_duration=op_duration,
            assignments=assignments, segments=segments,
        )

    # ── Resource no-overlap ────────────────────────────────────────────────────

    def _add_resource_nooverlap_constraints(
        self, per_operation: bool = True, per_operator: bool = True,
    ) -> None:
        """Two no-overlap families:

        * per operator   → an operator can run only one segment at a time;
        * per operation  → an operation is worked by one operator at a time
                          (sequential hand-off — no simultaneous double work).

        Le due famiglie sono disattivabili indipendentemente (diagnostica).
        """
        assert self.vars is not None
        v = self.vars
        model = self.model

        if not per_operation:
            logger.warning("DIAGNOSTIC: per-operation no-overlap DISABILITATO")
        if not per_operator:
            logger.warning("DIAGNOSTIC: per-operator no-overlap DISABILITATO")

        by_oper: dict[uuid.UUID, list[cp_model.IntervalVar]] = defaultdict(list)
        for _op_id, segs in v.segments.items():
            if per_operation and len(segs) >= 2:
                model.AddNoOverlap([sv.interval for sv in segs])
            for sv in segs:
                by_oper[sv.operator_id].append(sv.interval)

        if per_operator:
            for _oper_id, ivs in by_oper.items():
                if len(ivs) >= 2:
                    model.AddNoOverlap(ivs)

    # ── Precedence, RP-order, parent-wait, missing components ──────────────────

    def _add_precedence_constraints(self) -> None:
        """Enforce operation precedences and reference-point blocking."""
        assert self.vars is not None
        v = self.vars
        model = self.model

        for pred_id, succ_id in self.precedence_pairs:
            if pred_id in v.op_end and succ_id in v.op_start:
                model.Add(v.op_end[pred_id] <= v.op_start[succ_id])

        for op in self.operations:
            if op.reference_point_id is not None and op.id in v.op_start:
                blocking_min = self._blocking_constraints.get(op.id)
                if blocking_min is not None:
                    model.Add(v.op_start[op.id] >= blocking_min)

    def _add_missing_component_constraints(self) -> None:
        """Prevent operations from starting before their missing component arrives."""
        assert self.vars is not None
        v = self.vars
        model = self.model

        for op_id, earliest_start in self.missing_constraints.items():
            if op_id in v.op_start:
                model.Add(v.op_start[op_id] >= earliest_start)

    def _add_rp_order_constraints(self) -> None:
        """Enforce ordering between operation groups via the Reference Point DAG."""
        assert self.vars is not None
        v = self.vars
        model = self.model

        import logging
        _log = logging.getLogger(__name__)

        enforced = skipped = 0
        for idx, (ops_pred, ops_succ) in enumerate(self.rp_order_constraints):
            active_pred = [op_id for op_id in ops_pred if op_id in v.op_end]
            active_succ = [op_id for op_id in ops_succ if op_id in v.op_start]
            if not active_pred or not active_succ:
                skipped += 1
                continue

            completion = model.NewIntVar(0, self.horizon, f"rp_completion_{idx}")
            model.AddMaxEquality(completion, [v.op_end[op_id] for op_id in active_pred])
            for succ_op_id in active_succ:
                model.Add(v.op_start[succ_op_id] >= completion)
            enforced += 1

        _log.info("RP order constraints: %d enforced, %d skipped", enforced, skipped)

    def _add_parent_wait_constraints(self) -> None:
        """Vincolo Tipo A: l'operazione del padre aspetta il completamento del figlio target."""
        assert self.vars is not None
        v = self.vars
        model = self.model

        import logging
        _log = logging.getLogger(__name__)

        enforced = skipped = 0
        for idx, (ops_target, parent_op_id) in enumerate(self.parent_wait_constraints):
            active_target = [op_id for op_id in ops_target if op_id in v.op_end]
            if not active_target or parent_op_id not in v.op_start:
                skipped += 1
                continue

            completion = model.NewIntVar(0, self.horizon, f"pw_completion_{idx}")
            model.AddMaxEquality(completion, [v.op_end[op_id] for op_id in active_target])
            model.Add(v.op_start[parent_op_id] >= completion)
            enforced += 1

        _log.info("Parent-wait constraints: %d enforced, %d skipped", enforced, skipped)

    # ── Objective ──────────────────────────────────────────────────────────────

    def _operators_used_vars(self, tag: str) -> list[cp_model.BoolVar]:
        """One BoolVar per operator = 1 if it works at least one segment."""
        assert self.vars is not None
        v = self.vars
        model = self.model

        operator_ids = {oper_id for _, oper_id in v.assignments}
        used_vars: list[cp_model.BoolVar] = []
        for oper_id in operator_ids:
            used = model.NewBoolVar(f"oper_used_{tag}_{oper_id}")
            oper_assigns = [bv for (_op, oid), bv in v.assignments.items() if oid == oper_id]
            if oper_assigns:
                model.AddMaxEquality(used, oper_assigns)
            else:
                model.Add(used == 0)
            used_vars.append(used)
        return used_vars

    def _makespan_var(self, name: str) -> cp_model.IntVar | None:
        assert self.vars is not None
        v = self.vars
        if not v.op_end:
            return None
        makespan = self.model.NewIntVar(0, self.horizon, name)
        self.model.AddMaxEquality(makespan, list(v.op_end.values()))
        return makespan

    def _set_objective(self, objective_mode: str, params: dict) -> None:
        """Configure the CP-SAT optimisation objective.

        Lezione appresa: ogni operazione è interamente allocata (Σ size == residual),
        quindi l'utilizzo delle risorse è *strutturalmente costante*. Minimizzare il
        makespan non aggiunge valore per "max resources" ed è proprio ciò che rende il
        modello a segmenti intrattabile: spinge il solver a inseguire il bound LP e a
        non trovare MAI una prima soluzione (UNKNOWN / best:inf), pur esistendo.

        Strategia:
          * MAXIMIZE_RESOURCE_UTILIZATION → nessun obiettivo (pura fattibilità). Con
            l'orizzonte auto-dimensionato stretto (≈1.5× l'ottimo) qualunque soluzione
            fattibile è già compatta → schedule "senza buchi", trovato in secondi.
          * FINISH_BY_DATE → il target è un *vincolo* (makespan ≤ target), non un
            obiettivo da minimizzare.
          * MINIMIZE_OPERATORS / CUSTOM → mantengono il loro obiettivo esplicito.
        """
        assert self.vars is not None
        model = self.model

        if objective_mode == "MAXIMIZE_RESOURCE_UTILIZATION":
            return  # utilizzo costante → niente da ottimizzare: pura fattibilità

        if objective_mode == "FINISH_BY_DATE":
            if "target_finish_minutes" in params:
                makespan = self._makespan_var("makespan")
                if makespan is not None:
                    model.Add(makespan <= int(params["target_finish_minutes"]))
            return

        if objective_mode == "MINIMIZE_OPERATORS":
            used_vars = self._operators_used_vars("min")
            if used_vars:
                model.Minimize(sum(used_vars))

        elif objective_mode == "CUSTOM":
            SCALE = 1000
            weights = params.get("weights", {"makespan": 0.6, "operators": 0.4})
            makespan = self._makespan_var("makespan_custom")
            used_vars = self._operators_used_vars("cust")
            w_ms = int(weights.get("makespan", 0.6) * SCALE)
            w_ops = int(weights.get("operators", 0.4) * SCALE)
            obj = []
            if makespan is not None:
                obj.append(w_ms * makespan)
            if used_vars:
                obj.append(w_ops * sum(used_vars))
            if obj:
                model.Minimize(sum(obj))

    # ── Solution extraction ───────────────────────────────────────────────────

    def _extract_entries(self, scenario_id: uuid.UUID | None = None) -> list:
        """One ScheduleEntryCreate per *present* segment.

        A long operation split across shifts/operators yields several entries
        (the honest hand-off representation): same operation_id, different
        operator_id and time window.
        """
        from app.core.scheduler.shift_preprocessor import minutes_to_datetime
        from app.enums import ScheduleEntryStatus
        from app.schemas.schedule import ScheduleEntryCreate

        assert self.vars is not None
        v = self.vars
        _scenario_id = scenario_id or uuid.uuid4()

        entries = []
        for op in self.operations:
            segs = v.segments.get(op.id)
            if not segs:
                continue
            for sv in segs:
                if self.solver.Value(sv.present) != 1:
                    continue
                s_min = self.solver.Value(sv.start)
                e_min = self.solver.Value(sv.end)
                if e_min <= s_min:
                    continue
                entries.append(
                    ScheduleEntryCreate(
                        scenario_id=_scenario_id,
                        operation_id=op.id,
                        operator_id=sv.operator_id,
                        workcenter_id=op.workcenter_id,
                        scheduled_start=minutes_to_datetime(s_min, self.epoch),
                        scheduled_end=minutes_to_datetime(e_min, self.epoch),
                        status=ScheduleEntryStatus.SCHEDULED,
                    )
                )
        return entries

    def _count_operators_used(self) -> int:
        assert self.vars is not None
        used: set[uuid.UUID] = set()
        for (_op_id, oper_id), bv in self.vars.assignments.items():
            if self.solver.Value(bv) == 1:
                used.add(oper_id)
        return len(used)

    # ── Main entry point ──────────────────────────────────────────────────────

    def build_and_solve(
        self,
        objective_mode: str,
        params: dict,
        blocking_constraints: dict[uuid.UUID, int] | None = None,
        scenario_id: uuid.UUID | None = None,
        rp_order_constraints: list[tuple[list[uuid.UUID], list[uuid.UUID]]] | None = None,
        parent_wait_constraints: list[tuple[list[uuid.UUID], uuid.UUID]] | None = None,
    ) -> CpsatSolution:
        """Solve with slot-pruning; if that is INFEASIBLE, retry once *without* the
        cap to rule out a pruning-induced false negative before reporting INFEASIBLE.
        """
        import logging
        _log = logging.getLogger(__name__)

        self._blocking_constraints = blocking_constraints or {}
        self.rp_order_constraints = rp_order_constraints or []
        self.parent_wait_constraints = parent_wait_constraints or []

        self._slot_cap_factor = self.SLOT_CAPACITY_FACTOR
        result = self._assemble_and_solve(objective_mode, params, scenario_id)

        if result.status == "INFEASIBLE" and self._slot_cap_factor is not None:
            _log.warning(
                "INFEASIBLE con slot-pruning (factor=%s) — retry SENZA cap per "
                "escludere un falso negativo da pruning...", self._slot_cap_factor,
            )
            self._slot_cap_factor = None
            retry = self._assemble_and_solve(objective_mode, params, scenario_id)
            if retry.status != "INFEASIBLE":
                _log.warning(
                    "Retry senza cap → %s: il pruning aveva tagliato slot necessari. "
                    "Valuta di alzare CPSAT_SLOT_CAPACITY_FACTOR per evitare il doppio solve.",
                    retry.status,
                )
            else:
                _log.info(
                    "Retry senza cap: ancora INFEASIBLE → infeasibility REALE "
                    "(calendario/precedenze), non un artefatto di pruning.",
                )
            return retry

        return result

    def _assemble_and_solve(
        self, objective_mode: str, params: dict, scenario_id: uuid.UUID | None,
    ) -> CpsatSolution:
        """Build a fresh model with the current `_slot_cap_factor`, solve, return the solution."""
        import logging
        _log = logging.getLogger(__name__)

        # Fresh model/solver for this attempt (build_and_solve may call us twice).
        self.model = cp_model.CpModel()
        self.solver = cp_model.CpSolver()
        self._infeasibility_reasons = []

        if os.getenv("CPSAT_DISABLE_AUTOHORIZON", "0") != "1":
            self._autosize_horizon()
        self.vars = self._create_variables()
        _log.info("Dopo _create_variables: %d constraints", len(self.model.Proto().constraints))

        # ── Toggle diagnostici (env) per bisezionare l'infeasibility ──────────
        # Mettere a "1" per DISABILITARE la relativa famiglia di vincoli e isolare
        # quale rende il modello insoddisfacibile. Richiede riavvio del worker.
        def _disabled(name: str) -> bool:
            return os.getenv(name, "0") == "1"

        self._add_resource_nooverlap_constraints(
            per_operation=not _disabled("CPSAT_DISABLE_PER_OP_NOOVERLAP"),
            per_operator=not _disabled("CPSAT_DISABLE_PER_OPER_NOOVERLAP"),
        )
        _log.info("Dopo resource_nooverlap: %d constraints", len(self.model.Proto().constraints))
        if not _disabled("CPSAT_DISABLE_PRECEDENCE"):
            self._add_precedence_constraints()
        else:
            _log.warning("DIAGNOSTIC: precedence_pairs DISABILITATI")
        if not _disabled("CPSAT_DISABLE_RP_ORDER"):
            self._add_rp_order_constraints()
        else:
            _log.warning("DIAGNOSTIC: rp_order_constraints DISABILITATI")
        if not _disabled("CPSAT_DISABLE_PARENT_WAIT"):
            self._add_parent_wait_constraints()
        else:
            _log.warning("DIAGNOSTIC: parent_wait_constraints DISABILITATI")
        self._add_missing_component_constraints()
        _log.info("Dopo vincoli ordine/missing: %d constraints", len(self.model.Proto().constraints))
        if _disabled("CPSAT_FEASIBILITY_ONLY"):
            _log.warning("DIAGNOSTIC: obiettivo DISABILITATO — solo fattibilità")
        else:
            self._set_objective(objective_mode, params)

        self.solver.parameters.max_time_in_seconds = self.TIMEOUT
        self.solver.parameters.num_search_workers = min(8, max(1, os.cpu_count() or 1))
        self.solver.parameters.log_search_progress = True
        self.solver.parameters.linearization_level = 1

        has_objective = self.model.Proto().HasField("objective") or \
                        self.model.Proto().HasField("floating_point_objective")

        if has_objective:
            self.solver.parameters.stop_after_first_solution = False
            if objective_mode in ("MINIMIZE_OPERATORS", "MAXIMIZE_RESOURCE_UTILIZATION", "CUSTOM"):
                self.solver.parameters.max_time_in_seconds = max(self.TIMEOUT, 60)
            _log.info(
                "Obiettivo %s attivo — solver ottimizzerà per max %ds",
                objective_mode, self.solver.parameters.max_time_in_seconds,
            )
        else:
            self.solver.parameters.stop_after_first_solution = True
            _log.info("Nessun obiettivo — stop_after_first_solution=True")

        total_segments = sum(len(s) for s in self.vars.segments.values())
        _log.info(
            "CP-SAT solving: %d ops, %d operators, %d segments, horizon=%d min, timeout=%gs",
            len(self.operations), len(self.operators), total_segments,
            self.horizon, self.solver.parameters.max_time_in_seconds,
        )
        _log.info(
            "CP-SAT model size: %d KB, %d constraints, %d vars (assign=%d, segments=%d)",
            self.model.Proto().ByteSize() // 1024,
            len(self.model.Proto().constraints),
            len(self.model.Proto().variables),
            len(self.vars.assignments),
            total_segments,
        )

        status_code = self.solver.Solve(self.model)

        status_map = {
            cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE", cp_model.UNKNOWN: "UNKNOWN",
        }
        status_str = status_map.get(status_code, "UNKNOWN")
        _log.info("CP-SAT done in %.1fs: status=%s", self.solver.WallTime(), status_str)

        if status_code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            entries = self._extract_entries(scenario_id)
            makespan = (
                max(self.solver.Value(e) for e in self.vars.op_end.values())
                if self.vars.op_end else None
            )
            operators_used = self._count_operators_used()
            _log.info(
                "Soluzione %s: makespan=%s min, %d operatori usati, %d entries (segmenti)",
                status_str, makespan, operators_used, len(entries),
            )
            return CpsatSolution(
                status=status_str,
                schedule_entries=entries,
                makespan_minutes=makespan,
                operators_used=operators_used,
                solve_time_seconds=self.solver.WallTime(),
                conflicts=self._infeasibility_reasons,
            )

        return CpsatSolution(
            status=status_str,
            schedule_entries=[],
            makespan_minutes=None,
            operators_used=None,
            solve_time_seconds=self.solver.WallTime(),
            conflicts=self._infeasibility_reasons,
        )
