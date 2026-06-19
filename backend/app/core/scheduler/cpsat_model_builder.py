"""CP-SAT Model Builder — complete implementation (Steps 6a + 6b + 6c).

Phase 2 of the scheduling pipeline:
  _create_variables              — IntVar / IntervalVar / BoolVar
  _add_assignment_constraints    — at least 1 operator; SIMULTANEOUS duration scaling
  _add_shift_nooverlap_constraints — respect operator unavailability
  _add_operator_nooverlap_constraints — one operation at a time per operator
  _add_precedence_constraints    — end(pred) ≤ start(succ); RP blocking
  _add_missing_component_constraints — start ≥ component_arrival_minute
  _set_objective                 — FINISH_BY_DATE / MINIMIZE_OPERATORS /
                                   MAXIMIZE_RESOURCE_UTILIZATION / CUSTOM
  build_and_solve                — orchestrates everything, returns CpsatSolution
"""
from __future__ import annotations

from asyncio.log import logger
import math
import os
import uuid
from collections import defaultdict
from datetime import datetime

from ortools.sat.python import cp_model

from app.core.scheduler.cpsat_types import (
    CpsatSolution,
    CpsatVariables,
    QualifiedOperator,
    SchedulableOperation,
    operator_can_do,
)


class CpsatModelBuilder:
    """Builds and solves a CP-SAT scheduling model."""

    MIN_OP_DURATION: int = int(os.getenv("MIN_OP_DURATION_MINUTES", "30"))
    TIMEOUT: float = float(os.getenv("CPSAT_TIMEOUT_SECONDS", "30"))

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

        # Duration IntVars: variable duration per operation.
        # Created in _create_variables, constrained in _add_assignment_constraints.
        self._dur_vars: dict[uuid.UUID, cp_model.IntVar] = {}

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
            and len(oper.available_slots) > 0  # ← scarta chi non ha turni
        ]

    # ── Variable creation (Step 6a) ───────────────────────────────────────────

    def _create_variables(self) -> CpsatVariables:
        model = self.model
        self._dur_vars = {}
        op_start = {}
        op_end = {}
        op_interval = {}
        op_duration = {}
        assignments = {}
        opt_intervals = {}

        for op in self.operations:
            residual = self._compute_residual_duration(op)
            earliest = max(op.earliest_start_minutes, 0)
            
            # FIX: verifica che ci sia spazio nell'horizon
            latest_start = self.horizon - residual
            if latest_start < earliest:
                # Op non schedulabile: forza infeasibility esplicita
                self._infeasibility_reasons.append(
                    f"Op {op.id}: durata {residual} min non entra nell'horizon "
                    f"(earliest={earliest}, horizon={self.horizon})"
                )
                latest_start = earliest  # verrà rifiutato dai vincoli

            start = model.NewIntVar(earliest, self.horizon, f"start_{op.id}")
            end   = model.NewIntVar(earliest, self.horizon, f"end_{op.id}")
            
            # FIX: durata FISSA (no variabile) — semplifica enormemente il modello
            # dur_var fisso = residual (SIMULTANEOUS rimosso)
            dur_var = model.NewConstant(residual)
            self._dur_vars[op.id] = dur_var
            
            # Vincolo esplicito end = start + residual
            model.Add(end == start + residual)
            
            interval = model.NewIntervalVar(start, residual, end, f"interval_{op.id}")
            op_start[op.id] = start
            op_end[op.id] = end
            op_interval[op.id] = interval
            op_duration[op.id] = residual

            for oper in self._get_qualified_operators(op):
                key = (op.id, oper.id)
                assign = model.NewBoolVar(f"assign_{op.id}_{oper.id}")
                assignments[key] = assign
                opt_iv = model.NewOptionalIntervalVar(
                    start, residual, end, assign, f"opt_{op.id}_{oper.id}"
                )
                opt_intervals[key] = opt_iv

        return CpsatVariables(
            op_start=op_start, op_end=op_end, op_interval=op_interval,
            op_duration=op_duration, assignments=assignments,
            operator_optional_intervals=opt_intervals,
        )
    
    # ── Step 6b — Assignment and shift constraints ─────────────────────────────

    def _add_assignment_constraints(self) -> None:
        assert self.vars is not None
        v = self.vars
        model = self.model

        for op in self.operations:
            qualified = self._get_qualified_operators(op)
            if not qualified:
                self._infeasibility_reasons.append(
                    f"Op {op.id} ({op.operation_type.value}): nessun operatore qualificato"
                )
                model.Add(v.op_start[op.id] > self.horizon)
                continue

            assign_vars = [v.assignments[(op.id, oper.id)] for oper in qualified]
            
            # FIX: esattamente 1 operatore (non "almeno 1")
            # Questo rende il problema molto più semplice per il solver
            model.Add(sum(assign_vars) == 1)
            
    def _add_shift_nooverlap_constraints(self) -> None:
        """
        Versione v1: blocca solo gli operatori completamente indisponibili.
        
        Il vincolo "l'operazione non cade nei periodi di assenza" viene applicato
        solo a granularità giornaliera: se un operatore non ha nessuno slot in tutto
        l'horizon, viene escluso dall'assegnazione.
        
        NOTA: la versione completa con AddNoOverlap sui fixed intervals causa 
        INFEASIBLE perché le operazioni multi-turno (es. 480 min) non entrano
        in un singolo slot (max ~225 min). La modellazione corretta richiede
        la decomposizione dell'operazione in task-per-slot (v2).
        """
        v = self.vars
        model = self.model

        for oper in self.operators:
            if not oper.available_slots:
                for (op_id, oper_id), bv in v.assignments.items():
                    if oper_id == oper.id:
                        model.Add(bv == 0)

    def _add_operator_nooverlap_constraints(self) -> None:
        """Each operator can work on at most one operation at a time."""
        assert self.vars is not None
        v = self.vars
        model = self.model

        oper_ivs: dict[uuid.UUID, list[cp_model.IntervalVar]] = defaultdict(list)
        for (op_id, oper_id), iv in v.operator_optional_intervals.items():
            oper_ivs[oper_id].append(iv)

        for oper_id, ivs in oper_ivs.items():
            if len(ivs) >= 2:
                model.AddNoOverlap(ivs)

    # ── Step 6c — Precedence, missing components, objectives ──────────────────

    def _add_precedence_constraints(self) -> None:
        """Enforce operation precedences and reference-point blocking."""
        assert self.vars is not None
        v = self.vars
        model = self.model

        # Direct operation-level precedences.
        for pred_id, succ_id in self.precedence_pairs:
            if pred_id in v.op_end and succ_id in v.op_start:
                model.Add(v.op_end[pred_id] <= v.op_start[succ_id])

        # Reference-point blocking: machine-level operations cannot start
        # until their associated production order is complete.
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
        """Enforce ordering between operation groups via the Reference Point DAG.
 
        For each (ops_pred, ops_succ) in self.rp_order_constraints:
          - ops_pred: all schedulable op_ids of the predecessor RP's target order
                      AND all its BOM descendants (populated in reschedule_engine)
          - ops_succ: same for the successor RP's target order
 
        One auxiliary IntVar `completion` = max(op_end[p] for p in ops_pred).
        Every successor op must start >= completion.
 
        Why not blocking_constraints (old approach):
        - blocking_constraints needed pre-existing schedule_entries → empty on first run.
        - rp_order_constraints works on CP-SAT variables → correct on every run.
        """

        assert self.vars is not None
        v = self.vars
        model = self.model
 
        import logging
        _log = logging.getLogger(__name__)
 
        enforced = 0
        skipped = 0
 
        for idx, (ops_pred, ops_succ) in enumerate(self.rp_order_constraints):
            # Filter to ops actually present in this model run
            # (some may be COMPLETED and excluded from schedulable_ops)
            active_pred = [op_id for op_id in ops_pred if op_id in v.op_end]
            active_succ = [op_id for op_id in ops_succ if op_id in v.op_start]
 
            if not active_pred or not active_succ:
                skipped += 1
                continue
 
            # Auxiliary var: the moment all predecessor ops have finished
            completion = model.NewIntVar(0, self.horizon, f"rp_completion_{idx}")
            model.AddMaxEquality(completion, [v.op_end[op_id] for op_id in active_pred])
 
            # Every successor op must wait for that moment
            for succ_op_id in active_succ:
                model.Add(v.op_start[succ_op_id] >= completion)
 
            enforced += 1
 
        _log.info(
            "RP order constraints: %d enforced, %d skipped (no active ops on one side)",
            enforced, skipped,
        )

    def _add_parent_wait_constraints(self) -> None:
        """Vincolo Tipo A: l'operazione del padre aspetta il completamento del figlio target.

        Per ogni (ops_target, parent_op_id) in self.parent_wait_constraints:
        - ops_target: tutte le op schedulabili dell'ordine puntato dal RP + figli BOM
        - parent_op_id: l'op del livello padre che ha reference_point_id = quel RP

        Semantica: start(parent_op) >= max(end(op) for op in ops_target)

        Esempio concreto:
        op-MACH-2 (RP-M-02 → MA-001) NON PUÒ INIZIARE
        finché non finiscono TUTTE le op di MA-001 + AGG-001..005 + GRP-001..020
        """
        assert self.vars is not None
        v = self.vars
        model = self.model

        import logging
        _log = logging.getLogger(__name__)

        enforced = 0
        skipped = 0

        for idx, (ops_target, parent_op_id) in enumerate(self.parent_wait_constraints):
            # Solo le op effettivamente presenti nel modello (alcune potrebbero essere COMPLETED)
            active_target = [op_id for op_id in ops_target if op_id in v.op_end]

            if not active_target:
                # Il target è già completamente finito (tutte COMPLETED) → nessun vincolo
                skipped += 1
                continue

            if parent_op_id not in v.op_start:
                skipped += 1
                continue

            # Variabile ausiliaria: il momento in cui finisce l'ULTIMA op del target
            completion = model.NewIntVar(0, self.horizon, f"pw_completion_{idx}")
            model.AddMaxEquality(completion, [v.op_end[op_id] for op_id in active_target])

            # L'operazione padre non può iniziare prima di quel momento
            model.Add(v.op_start[parent_op_id] >= completion)

            enforced += 1

        _log.info(
            "Parent-wait constraints: %d enforced, %d skipped",
            enforced, skipped,
        )

    def _set_objective(self, objective_mode: str, params: dict) -> None:
        """Configure the CP-SAT optimisation objective."""
        assert self.vars is not None
        v = self.vars
        model = self.model

        if objective_mode == "FINISH_BY_DATE":
            if not v.op_end:
                return
            makespan = model.NewIntVar(0, self.horizon, "makespan")
            model.AddMaxEquality(makespan, list(v.op_end.values()))
            model.Minimize(makespan)
            # AM3 - TEMPORANEAMENTE COMMENTATO per debug poer vedere se il problema della non solzuione è il tempo ristretto
            if "target_finish_minutes" in params:
                model.Add(makespan <= int(params["target_finish_minutes"]))

        elif objective_mode == "MINIMIZE_OPERATORS":
            operator_ids = {oper_id for _, oper_id in v.assignments}
            used_vars: list[cp_model.BoolVar] = []
            for oper_id in operator_ids:
                used = model.NewBoolVar(f"oper_used_{oper_id}")
                oper_assigns = [
                    bv for (op_id, oid), bv in v.assignments.items()
                    if oid == oper_id
                ]
                if oper_assigns:
                    model.AddMaxEquality(used, oper_assigns)
                else:
                    model.Add(used == 0)
                used_vars.append(used)
            if used_vars:
                model.Minimize(sum(used_vars))

        elif objective_mode == "MAXIMIZE_RESOURCE_UTILIZATION":
            # Maximise total planned work assigned (integer residuals as coefficients).
            terms = [
                assign_bv * v.op_duration.get(op_id, 0)
                for (op_id, oper_id), assign_bv in v.assignments.items()
            ]
            if terms:
                model.Maximize(sum(terms))

        elif objective_mode == "CUSTOM":
            SCALE = 1000
            weights = params.get(
                "weights", {"makespan": 0.5, "operators": 0.3, "utilization": 0.2}
            )

            makespan = model.NewIntVar(0, self.horizon, "makespan_custom")
            if v.op_end:
                model.AddMaxEquality(makespan, list(v.op_end.values()))
            w_ms = int(weights.get("makespan", 0.5) * SCALE)

            operator_ids = {oper_id for _, oper_id in v.assignments}
            used_list: list[cp_model.BoolVar] = []
            for oper_id in operator_ids:
                used = model.NewBoolVar(f"oper_used_cust_{oper_id}")
                oper_assigns = [
                    bv for (op_id, oid), bv in v.assignments.items()
                    if oid == oper_id
                ]
                if oper_assigns:
                    model.AddMaxEquality(used, oper_assigns)
                else:
                    model.Add(used == 0)
                used_list.append(used)
            w_ops = int(weights.get("operators", 0.3) * SCALE)

            util_terms = [
                assign_bv * v.op_duration.get(op_id, 0)
                for (op_id, oper_id), assign_bv in v.assignments.items()
            ]
            max_util = max(1, sum(v.op_duration.values()) * max(1, len(self.operators)))
            total_util = model.NewIntVar(0, max_util, "total_util_cust")
            if util_terms:
                model.Add(total_util == sum(util_terms))
            w_util = int(weights.get("utilization", 0.2) * SCALE)

            obj = (
                w_ms * makespan
                + w_ops * (sum(used_list) if used_list else model.NewConstant(0))
                - w_util * total_util
            )
            model.Minimize(obj)

    # ── Solution extraction helper ────────────────────────────────────────────

    def _extract_entries(self, scenario_id: uuid.UUID | None = None) -> list:
        from app.core.scheduler.shift_preprocessor import minutes_to_datetime
        from app.enums import ScheduleEntryStatus
        from app.schemas.schedule import ScheduleEntryCreate

        assert self.vars is not None
        v = self.vars
        _scenario_id = scenario_id or uuid.uuid4()
        entries = []

        for op in self.operations:
            if op.id not in v.op_start:
                continue

            start_min = self.solver.Value(v.op_start[op.id])
            end_min   = self.solver.Value(v.op_end[op.id])
            s_start   = minutes_to_datetime(start_min, self.epoch)
            s_end     = minutes_to_datetime(end_min, self.epoch)

            for (op_id, oper_id), bv in v.assignments.items():
                if op_id == op.id and self.solver.Value(bv) == 1:
                    entries.append(
                        ScheduleEntryCreate(
                            scenario_id=_scenario_id,
                            operation_id=op.id,
                            operator_id=oper_id,
                            workcenter_id=op.workcenter_id,
                            scheduled_start=s_start,
                            scheduled_end=s_end,
                            status=ScheduleEntryStatus.SCHEDULED,
                        )
                    )
        return entries

    # ── Main entry point ──────────────────────────────────────────────────────

    def build_and_solve(
        self,
        objective_mode: str,
        params: dict,
        blocking_constraints: dict[uuid.UUID, int] | None = None,
        scenario_id: uuid.UUID | None = None,
        rp_order_constraints: list[tuple[list[uuid.UUID], list[uuid.UUID]]] | None = None,
        parent_wait_constraints: list[tuple[list[uuid.UUID], uuid.UUID]] | None = None,  # ← NUOVO
    ) -> CpsatSolution:
    
        """Assemble the full model, run the solver, return CpsatSolution.

        Args:
            objective_mode: One of FINISH_BY_DATE | MINIMIZE_OPERATORS |
                            MAXIMIZE_RESOURCE_UTILIZATION | CUSTOM.
            params: Objective-specific parameters (e.g. target_finish_minutes,
                    weights dict for CUSTOM).
            blocking_constraints: {op_id → min_start_minute} for machine-level
                operations blocked by reference-point precedences.
            scenario_id: UUID to stamp on generated ScheduleEntryCreate objects.
        """
        import logging
        _log = logging.getLogger(__name__)

        self._blocking_constraints = blocking_constraints or {}
        self.rp_order_constraints = rp_order_constraints or []
        self.parent_wait_constraints = parent_wait_constraints or [] 
        self._infeasibility_reasons = []

        self.vars = self._create_variables()
        
        
        _log.info("Dopo _create_variables: %d constraints", len(self.model.Proto().constraints))

        self._add_assignment_constraints()
        _log.info("Dopo assignment: %d constraints", len(self.model.Proto().constraints))
        self._add_shift_nooverlap_constraints()
        _log.info("Dopo shift_nooverlap: %d constraints", len(self.model.Proto().constraints))
        self._add_operator_nooverlap_constraints()
        _log.info("Dopo operator_nooverlap: %d constraints", len(self.model.Proto().constraints))      
        self._add_precedence_constraints()
        _log.info("Dopo precedence_constraints: %d constraints", len(self.model.Proto().constraints))
        self._add_rp_order_constraints()
        _log.info("Dopo rp_order_constraints: %d constraints", len(self.model.Proto().constraints))
        self._add_parent_wait_constraints()
        _log.info("Dopo parent_wait_constraints: %d constraints", len(self.model.Proto().constraints))
        self._add_missing_component_constraints()
        _log.info("Dopo missing_component_constraints: %d constraints", len(self.model.Proto().constraints))  
        self._set_objective(objective_mode, params)

        """
        PATCH: cpsat_model_builder.py — Fix obiettivi troppo simili
        
        PROBLEMA:
        solver.parameters.stop_after_first_solution = True
        fa sì che il solver trovi UNA soluzione e si fermi, ignorando l'obiettivo.
        MINIMIZE_OPERATORS e MAXIMIZE_RESOURCE_UTILIZATION danno risultati identici.
        
        FIX:
        - stop_after_first_solution = True SOLO se non c'è obiettivo (soddisfacibilità pura)
        - Per gli obiettivi reali, dare al solver tempo di ottimizzare
        - Aggiungere solution callback per log progressivo
        
        DOVE APPLICARE:
        In build_and_solve(), DOPO la chiamata a _set_objective(),
        SOSTITUIRE il blocco dei parametri solver.
        """

        self.solver.parameters.max_time_in_seconds = self.TIMEOUT
        self.solver.parameters.num_search_workers = min(8, max(1, os.cpu_count() or 1))
        self.solver.parameters.log_search_progress = True
        self.solver.parameters.linearization_level = 1
        #self.solver.parameters.search_branching = 6  # PORTFOLIO_WITH_QUICK_RESTART
 
        # ── CRITICO: stop_after_first_solution SOLO se non c'è obiettivo ──
        # Se il solver ha un obiettivo (Minimize/Maximize), deve avere tempo
        # per ottimizzare. Altrimenti trova la stessa prima soluzione per tutti
        # gli obiettivi, rendendo MINIMIZE_OPERATORS e MAXIMIZE_UTILIZATION identici.
        has_objective = self.model.Proto().HasField("objective") or \
                        self.model.Proto().HasField("floating_point_objective")
        
        if has_objective:
            # Dai tempo al solver di ottimizzare, ma con un limite ragionevole
            self.solver.parameters.stop_after_first_solution = False
            # Per obiettivi che richiedono esplorazione, usa timeout più lungo
            if objective_mode in ("MINIMIZE_OPERATORS", "MAXIMIZE_RESOURCE_UTILIZATION", "CUSTOM"):
                self.solver.parameters.max_time_in_seconds = max(self.TIMEOUT, 60)
            _log.info(
                "Obiettivo %s attivo — solver ottimizzerà per max %ds",
                objective_mode,
                self.solver.parameters.max_time_in_seconds,
            )
        else:
            # Nessun obiettivo → soddisfacibilità pura, prima soluzione va bene
            self.solver.parameters.stop_after_first_solution = True
            _log.info("Nessun obiettivo — stop_after_first_solution=True")

        
        
        _log.info(
            "CP-SAT solving: %d ops, %d operators, horizon=%d min, timeout=%gs",
            len(self.operations), len(self.operators), self.horizon, self.TIMEOUT
        )

        _log.info(
            "CP-SAT model size: %d KB, %d constraints, %d vars (assign=%d, optional_iv=%d)",
            self.model.Proto().ByteSize() // 1024,
            len(self.model.Proto().constraints),
            len(self.model.Proto().variables),
            len(self.vars.assignments),
            len(self.vars.operator_optional_intervals),
        )       

        
        self._add_solution_hints()

        status_code = self.solver.Solve(self.model)

        _log.info(
            "CP-SAT done in %.1fs: status=%s",
            self.solver.WallTime(),
            {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
             cp_model.INFEASIBLE: "INFEASIBLE", cp_model.UNKNOWN: "UNKNOWN"}.get(status_code, "?"),
        )

        if status_code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            _log.info(
                "Solver status=%s objective_value=%s wall_time=%.1fs",
                "OPTIMAL" if status_code == cp_model.OPTIMAL else "FEASIBLE",
                self.solver.ObjectiveValue() if has_objective else "N/A",
                self.solver.WallTime(),
            )
            
            # Log specifico per obiettivo
            if objective_mode == "MINIMIZE_OPERATORS":
                # Conta operatori effettivamente usati
                used_count = sum(
                    1 for oper_id in {oid for _, oid in self.vars.assignments}
                    if any(
                        self.solver.Value(bv) == 1
                        for (op_id, oid2), bv in self.vars.assignments.items()
                        if oid2 == oper_id
                    )
                )
                _log.info("MINIMIZE_OPERATORS: %d operatori usati su %d disponibili",
                          used_count, len(self.operators))
            
            elif objective_mode == "MAXIMIZE_RESOURCE_UTILIZATION":
                total_work = sum(
                    self.vars.op_duration.get(op_id, 0)
                    for (op_id, oper_id), bv in self.vars.assignments.items()
                    if self.solver.Value(bv) == 1
                )
                _log.info("MAXIMIZE_UTILIZATION: %d minuti totali di lavoro assegnati", total_work)


        status_map = {
            cp_model.OPTIMAL:    "OPTIMAL",
            cp_model.FEASIBLE:   "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.UNKNOWN:    "UNKNOWN",
        }
        status_str = status_map.get(status_code, "UNKNOWN")

        if status_code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            entries = self._extract_entries(scenario_id)

            makespan = (
                max(self.solver.Value(v) for v in self.vars.op_end.values())
                if self.vars.op_end else None
            )

            all_oper_ids = {oid for _, oid in self.vars.assignments}
            operators_used = sum(
                1 for oper_id in all_oper_ids
                if any(
                    self.solver.Value(bv) == 1
                    for (op_id, oid), bv in self.vars.assignments.items()
                    if oid == oper_id
                )
            )

            return CpsatSolution(
                status=status_str,
                schedule_entries=entries,
                makespan_minutes=makespan,
                operators_used=operators_used,
                solve_time_seconds=self.solver.WallTime(),
                conflicts=self._infeasibility_reasons,
            )
        else:
            return CpsatSolution(
                status=status_str,
                schedule_entries=[],
                makespan_minutes=None,
                operators_used=None,
                solve_time_seconds=self.solver.WallTime(),
                conflicts=self._infeasibility_reasons,
            )

    def _add_solution_hints(self) -> None:
        """Fornisce al solver una soluzione iniziale euristica (greedy)."""
        assert self.vars is not None
        v = self.vars

        # Per ogni operazione, assegna il primo operatore disponibile e
        # suggerisci uno start greedy (il primo slot libero)
        oper_busy_until: dict[uuid.UUID, int] = {}

        for op in sorted(self.operations, key=lambda o: o.earliest_start_minutes):
            residual = self._compute_residual_duration(op)
            qualified = self._get_qualified_operators(op)
            if not qualified:
                continue

            chosen_oper = None
            chosen_start = None

            for oper in qualified:
                busy = oper_busy_until.get(oper.id, 0)
                candidate_start = max(op.earliest_start_minutes, busy)
                
                # Trova il primo slot disponibile
                for slot_s, slot_e in oper.available_slots:
                    if slot_e - slot_s < residual:
                        continue
                    actual_start = max(candidate_start, slot_s)
                    if actual_start + residual <= slot_e:
                        chosen_oper = oper
                        chosen_start = actual_start
                        break
                if chosen_oper:
                    break

            if chosen_oper and chosen_start is not None:
                self.model.AddHint(v.op_start[op.id], chosen_start)
                self.model.AddHint(v.op_end[op.id], chosen_start + residual)
                for oper in qualified:
                    key = (op.id, oper.id)
                    if key in v.assignments:
                        self.model.AddHint(
                            v.assignments[key], 
                            1 if oper.id == chosen_oper.id else 0
                        )
                oper_busy_until[chosen_oper.id] = chosen_start + residual
