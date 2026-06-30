"""CP-SAT cumulativo sul modello a CAPACITÀ DI GRUPPO (RCPSP a minuti).

Perché ora CP-SAT è trattabile (mentre il vecchio modello a segmenti per-operatore
era UNKNOWN): il modello a capacità elimina i due killer —
  1. niente operatori "con nome" → niente simmetria;
  2. la contesa risorse è un singolo `AddCumulative` per gruppo (capacità = count
     operazioni in parallelo), NON migliaia di `NoOverlap` a coppie.

Modello (precisione al minuto):
  * Ogni operazione → pochi segmenti opzionali (preemption su più giorni),
    `Σ size == durata_residua`, `NoOverlap` per-operazione (una risorsa alla volta).
  * Ogni gruppo (workcenter+skill) → `AddCumulative(segmenti+blocker, demand, count)`.
  * Calendario: intervalli "bloccanti" a piena capacità fuori orario → i segmenti
    restano nelle finestre lavorative.
  * Precedenze / componenti mancanti / IN_PROGRESS come nel resto del motore.
  * Obiettivo: minimizza makespan (e `makespan ≤ target` per FINISH_BY_DATE).

Pensato per girare DOPO il greedy (`capacity_scheduler.py`): il greedy fornisce un
orizzonte stretto e un warm-start (hint), così CP-SAT ottimizza invece di cercare
una prima soluzione da zero. Se va in timeout senza soluzione, l'orchestratore usa
il greedy come fallback.
"""
from __future__ import annotations

import logging
import math
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from ortools.sat.python import cp_model

from app.core.scheduler.capacity_scheduler import (
    CapacityEntry,
    CapacityResult,
    ResourceGroup,
)
from app.core.scheduler.cpsat_types import SchedulableOperation, _SKILL_CAN_DO

logger = logging.getLogger(__name__)

DAY_MINUTES = 1440


@dataclass
class _Seg:
    group: ResourceGroup
    present: cp_model.IntVar
    start: cp_model.IntVar
    size: cp_model.IntVar
    end: cp_model.IntVar
    interval: cp_model.IntervalVar


class CapacityCpsatScheduler:
    def __init__(
        self,
        operations: list[SchedulableOperation],
        resource_groups: list[ResourceGroup],
        horizon_minutes: int,
        epoch: datetime,
        precedence_pairs: list[tuple[uuid.UUID, uuid.UUID]] | None = None,
        rp_order_constraints: list[tuple[list[uuid.UUID], list[uuid.UUID]]] | None = None,
        parent_wait_constraints: list[tuple[list[uuid.UUID], uuid.UUID]] | None = None,
        missing_constraints: dict[uuid.UUID, int] | None = None,
        objective_mode: str = "MAXIMIZE_RESOURCE_UTILIZATION",
        target_finish_minutes: int | None = None,
        day_start_offset_minutes: int = 8 * 60,
        working_weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4}),
        timeout_seconds: float = 30.0,
        max_segments_per_op_group: int = 6,
        warm_start: CapacityResult | None = None,
    ) -> None:
        self.operations = operations
        self.groups = resource_groups
        self.horizon = max(1, int(horizon_minutes))
        self.epoch = epoch
        self.precedence_pairs = precedence_pairs or []
        self.rp_order_constraints = rp_order_constraints or []
        self.parent_wait_constraints = parent_wait_constraints or []
        self.missing_constraints = missing_constraints or {}
        self.objective_mode = objective_mode
        self.target_finish_minutes = target_finish_minutes
        self.day_start = day_start_offset_minutes
        self.working_weekdays = working_weekdays
        self.timeout = timeout_seconds
        self.max_k = max_segments_per_op_group
        self.warm_start = warm_start

        self.model = cp_model.CpModel()
        self.solver = cp_model.CpSolver()
        self._op_index = {op.id: op for op in operations}

    # ── helpers ──────────────────────────────────────────────────────────────

    def _residual(self, op: SchedulableOperation) -> int:
        return max(0, int(math.ceil(op.planned_duration_minutes * (1.0 - op.progress_pct / 100.0))))

    def _candidate_groups(self, op: SchedulableOperation) -> list[ResourceGroup]:
        return [
            g for g in self.groups
            if g.workcenter_id == op.workcenter_id
            and op.operation_type in _SKILL_CAN_DO.get(g.skill, set())
        ]

    def _weekday(self, day_index: int) -> int:
        return (self.epoch + timedelta(days=day_index)).weekday()

    def _capacity_blockers(self, g: ResourceGroup, cap: int) -> list[tuple[int, int, int]]:
        """(start, end, demand) per modellare la capacità VARIABILE per giorno con un
        `AddCumulative` a capacità costante `cap` = max risorse del gruppo:
          * fuori orario o giorni a 0 risorse → demand = cap (blocco pieno);
          * giorni con count < cap → "riduttore" demand = (cap − count) sulla finestra.
        Le ore/giorno variano per giorno (weekday_minutes)."""
        out: list[tuple[int, int, int]] = []
        H = self.horizon
        days = H // DAY_MINUTES + 2
        for d in range(days):
            day0 = d * DAY_MINUTES
            if day0 >= H:
                break
            day_end = min(day0 + DAY_MINUTES, H)
            wd = self._weekday(d)
            c = g.count_for(wd)
            mins = g.minutes_for(wd)
            if c <= 0 or mins <= 0:
                out.append((day0, day_end, cap))           # giorno intero non lavorato
                continue
            ws = day0 + self.day_start
            we = min(ws + mins, day_end)
            if ws > day0:
                out.append((day0, ws, cap))                # mattina prima della finestra
            if day_end > we:
                out.append((we, day_end, cap))             # sera dopo la finestra
            if c < cap and we > ws:
                out.append((ws, we, cap - c))              # riduttore di capacità
        return [(s, e, dem) for (s, e, dem) in out if e > s and dem > 0]

    def _preds(self) -> dict[uuid.UUID, set[uuid.UUID]]:
        ids = set(self._op_index)
        preds: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        # UNICO vincolo HARD: BOM ordine-livello.
        # precedence_pairs (rp_direct_pairs) NON è hard: RP ordering è SOFT priority
        # gestito via op_priority nell'obiettivo. Se op1 è bloccata si può lavorare op2.
        for ops_target, parent in self.parent_wait_constraints:
            if parent in ids:
                for t in (x for x in ops_target if x in ids):
                    preds[parent].add(t)
        return preds

    # ── build & solve ────────────────────────────────────────────────────────

    def solve(self) -> CapacityResult:
        model = self.model
        H = self.horizon

        op_start: dict[uuid.UUID, cp_model.IntVar] = {}
        op_end: dict[uuid.UUID, cp_model.IntVar] = {}
        op_segs: dict[uuid.UUID, list[_Seg]] = {}
        group_intervals: dict[tuple, list[cp_model.IntervalVar]] = defaultdict(list)
        group_demands: dict[tuple, list[int]] = defaultdict(list)
        conflicts: list[str] = []

        for op in self.operations:
            D = self._residual(op)
            est = max(op.earliest_start_minutes, self.missing_constraints.get(op.id, 0))
            if D <= 0:
                continue
            groups = self._candidate_groups(op)
            if not groups:
                conflicts.append(
                    f"Op {op.id} ({op.operation_type.value}): nessun gruppo risorse compatibile"
                )
                continue

            segs: list[_Seg] = []
            eff_starts: list[cp_model.IntVar] = []
            eff_ends: list[cp_model.IntVar] = []
            for g in groups:
                gkey = (g.workcenter_id, g.skill)
                day_len = max(1, g.max_minutes)          # finestra giornaliera più lunga del gruppo
                k = min(self.max_k, max(1, math.ceil(D / day_len) + 1))
                seg_cap = min(day_len, D)                 # un segmento ≤ finestra di una risorsa
                for i in range(k):
                    tag = f"{op.id}_{g.skill.value}_{i}"
                    pres = model.NewBoolVar(f"p_{tag}")
                    size = model.NewIntVar(0, seg_cap, f"sz_{tag}")
                    start = model.NewIntVar(est, H, f"s_{tag}")
                    end = model.NewIntVar(est, H, f"e_{tag}")
                    iv = model.NewOptionalIntervalVar(start, size, end, pres, f"iv_{tag}")
                    model.Add(size == 0).OnlyEnforceIf(pres.Not())
                    model.Add(size >= 1).OnlyEnforceIf(pres)
                    eff_s = model.NewIntVar(0, H, f"es_{tag}")
                    model.Add(eff_s == start).OnlyEnforceIf(pres)
                    model.Add(eff_s == H).OnlyEnforceIf(pres.Not())
                    eff_e = model.NewIntVar(0, H, f"ee_{tag}")
                    model.Add(eff_e == end).OnlyEnforceIf(pres)
                    model.Add(eff_e == 0).OnlyEnforceIf(pres.Not())
                    eff_starts.append(eff_s)
                    eff_ends.append(eff_e)
                    sv = _Seg(g, pres, start, size, end, iv)
                    segs.append(sv)
                    group_intervals[gkey].append(iv)
                    group_demands[gkey].append(1)

            model.Add(sum(s.size for s in segs) == D)            # tutto il lavoro allocato
            if len(segs) >= 2:
                model.AddNoOverlap([s.interval for s in segs])   # una risorsa alla volta

            os_v = model.NewIntVar(0, H, f"ostart_{op.id}")
            oe_v = model.NewIntVar(0, H, f"oend_{op.id}")
            model.AddMinEquality(os_v, eff_starts)
            model.AddMaxEquality(oe_v, eff_ends)
            op_start[op.id] = os_v
            op_end[op.id] = oe_v
            op_segs[op.id] = segs

        # ── Cumulative per gruppo (+ blocker calendario) ──────────────────────
        for g in self.groups:
            gkey = (g.workcenter_id, g.skill)
            ivs = list(group_intervals.get(gkey, []))
            dems = list(group_demands.get(gkey, []))
            if not ivs:
                continue
            cap = max(1, g.max_count)
            for (b0, b1, dem) in self._capacity_blockers(g, cap):
                blk = model.NewIntervalVar(b0, b1 - b0, b1, f"blk_{g.skill.value}_{b0}")
                ivs.append(blk)
                dems.append(dem)
            model.AddCumulative(ivs, dems, cap)

        # ── Precedenze ────────────────────────────────────────────────────────
        preds = self._preds()
        for succ, pset in preds.items():
            if succ not in op_start:
                continue
            for p in pset:
                if p in op_end:
                    model.Add(op_start[succ] >= op_end[p])

        # ── Raccoglie 'present' per gruppo (usato dagli obiettivi multi-criterio) ──
        group_pres: dict[tuple, list] = defaultdict(list)
        for segs_list in op_segs.values():
            for s in segs_list:
                group_pres[(s.group.workcenter_id, s.group.skill)].append(s.present)

        # ── Obiettivo ─────────────────────────────────────────────────────────
        if op_end:
            makespan = model.NewIntVar(0, H, "makespan")
            model.AddMaxEquality(makespan, list(op_end.values()))

            if self.objective_mode == "FINISH_BY_DATE":
                # Vincolo hard: finisci entro la data target. Poi minimizza makespan.
                if self.target_finish_minutes:
                    model.Add(makespan <= int(self.target_finish_minutes))
                model.Minimize(makespan)

            elif self.objective_mode == "MINIMIZE_OPERATORS":
                # Obiettivo primario: minimizza il numero di gruppi-risorsa distinti usati
                # (concentra lavoro su meno risorse → turni ridotti, crew minima).
                # Obiettivo secondario: minimizza makespan (a parità di gruppi).
                # Scala: 1 gruppo risparmiato > qualsiasi riduzione di makespan.
                used_vars = []
                for gkey, pres_list in group_pres.items():
                    if not pres_list:
                        continue
                    uid = f"{str(gkey[0])[:8]}_{gkey[1].value}"
                    used_g = model.NewBoolVar(f"used_g_{uid}")
                    model.AddBoolOr(pres_list).OnlyEnforceIf(used_g)
                    model.AddBoolAnd([p.Not() for p in pres_list]).OnlyEnforceIf(used_g.Not())
                    used_vars.append(used_g)
                if used_vars:
                    model.Minimize(sum(used_vars) * (H + 1) + makespan)
                else:
                    model.Minimize(makespan)

            elif self.objective_mode == "MAXIMIZE_RESOURCE_UTILIZATION":
                # Obiettivo primario: minimizza makespan (stessa durata → più throughput).
                # Obiettivo secondario: massimizza i gruppi-risorsa effettivamente usati
                # (distribuisce il lavoro → nessuna risorsa rimane idle mentre un'altra è satura).
                # Scala: 1 minuto di makespan > 1 gruppo aggiuntivo usato.
                used_vars = []
                for gkey, pres_list in group_pres.items():
                    if not pres_list:
                        continue
                    uid = f"{str(gkey[0])[:8]}_{gkey[1].value}"
                    used_g = model.NewBoolVar(f"used_g_{uid}")
                    model.AddBoolOr(pres_list).OnlyEnforceIf(used_g)
                    model.AddBoolAnd([p.Not() for p in pres_list]).OnlyEnforceIf(used_g.Not())
                    used_vars.append(used_g)
                n_g = max(len(used_vars), 1)
                if used_vars:
                    model.Minimize(makespan * (n_g + 1) - sum(used_vars))
                else:
                    model.Minimize(makespan)

            else:
                # CUSTOM o qualsiasi valore non riconosciuto → minimizza makespan puro
                model.Minimize(makespan)

        self._apply_warm_start(op_segs)

        # ── Solve ─────────────────────────────────────────────────────────────
        self.solver.parameters.max_time_in_seconds = self.timeout
        self.solver.parameters.num_search_workers = 8
        n_segments = sum(len(s) for s in op_segs.values())
        logger.info(
            "CP-SAT capacità: %d op, %d gruppi, %d segmenti, horizon=%d min, timeout=%gs",
            len(op_segs), len(self.groups), n_segments, H, self.timeout,
        )
        status = self.solver.Solve(self.model)
        status_str = {
            cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE", cp_model.UNKNOWN: "UNKNOWN",
        }.get(status, "UNKNOWN")
        logger.info("CP-SAT capacità done in %.2fs: status=%s", self.solver.WallTime(), status_str)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return CapacityResult(status=status_str, entries=[], makespan_minutes=None,
                                  conflicts=conflicts)

        entries: list[CapacityEntry] = []
        completion: dict[uuid.UUID, int] = {}
        for op_id, segs in op_segs.items():
            last = 0
            for s in segs:
                if self.solver.Value(s.present) == 1 and self.solver.Value(s.size) > 0:
                    st = self.solver.Value(s.start)
                    en = self.solver.Value(s.end)
                    entries.append(CapacityEntry(
                        operation_id=op_id, workcenter_id=s.group.workcenter_id,
                        resource_type_id=s.group.resource_type_id, skill=s.group.skill,
                        lane_index=0, start_minutes=st, end_minutes=en,
                    ))
                    last = max(last, en)
            completion[op_id] = last
        makespan_min = max(completion.values(), default=0) if entries else None
        return CapacityResult(
            status="OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            entries=entries, makespan_minutes=makespan_min, conflicts=conflicts,
            completion=completion,
        )

    def _apply_warm_start(self, op_segs: dict[uuid.UUID, list[_Seg]]) -> None:
        """Hint dal greedy: per ogni op, suggerisce la presenza/posizione del primo
        segmento del gruppo giusto. Aiuta CP-SAT a partire da una soluzione."""
        if not self.warm_start or not self.warm_start.entries:
            return
        by_op: dict[uuid.UUID, list] = defaultdict(list)
        for e in self.warm_start.entries:
            by_op[e.operation_id].append(e)
        applied = 0
        for op_id, blocks in by_op.items():
            segs = op_segs.get(op_id)
            if not segs:
                continue
            blocks = sorted(blocks, key=lambda b: b.start_minutes)
            for blk, seg in zip(blocks, segs):
                if seg.group.skill != blk.skill:
                    continue
                try:
                    self.model.AddHint(seg.present, 1)
                    self.model.AddHint(seg.start, blk.start_minutes)
                    self.model.AddHint(seg.size, max(1, blk.end_minutes - blk.start_minutes))
                    applied += 1
                except Exception:
                    pass
        if applied:
            logger.info("CP-SAT capacità: warm-start applicato a %d segmenti", applied)
