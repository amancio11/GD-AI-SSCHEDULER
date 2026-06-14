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
        """Create all CP-SAT decision variables.

        For each operation:
          - start / end     IntVar [earliest_start, horizon]
          - dur_var         IntVar [MIN_OP, residual]  — constrained later
          - interval        IntervalVar(start, dur_var, end)

        For each (operation, qualified_operator):
          - assign          BoolVar
          - opt_interval    OptionalIntervalVar(start, dur_var, end, assign)
        """
        model = self.model
        self._dur_vars = {}

        op_start:   dict[uuid.UUID, cp_model.IntVar]       = {}
        op_end:     dict[uuid.UUID, cp_model.IntVar]       = {}
        op_interval: dict[uuid.UUID, cp_model.IntervalVar] = {}
        op_duration: dict[uuid.UUID, int]                  = {}
        assignments: dict[tuple[uuid.UUID, uuid.UUID], cp_model.BoolVar]       = {}
        opt_intervals: dict[tuple[uuid.UUID, uuid.UUID], cp_model.IntervalVar] = {}

        for op in self.operations:
            residual = self._compute_residual_duration(op)
            earliest = max(op.earliest_start_minutes, 0)

            start = model.NewIntVar(earliest, self.horizon, f"start_{op.id}")
            end   = model.NewIntVar(earliest, self.horizon, f"end_{op.id}")

            # Variable duration — fixed or reduced in _add_assignment_constraints
            dur_var = model.NewIntVar(self.MIN_OP_DURATION, residual, f"dur_{op.id}")
            self._dur_vars[op.id] = dur_var

            interval = model.NewIntervalVar(start, dur_var, end, f"interval_{op.id}")

            op_start[op.id]    = start
            op_end[op.id]      = end
            op_interval[op.id] = interval
            op_duration[op.id] = residual  # integer residual for objective coefficients

            for oper in self._get_qualified_operators(op):
                key = (op.id, oper.id)
                assign = model.NewBoolVar(f"assign_{op.id}_{oper.id}")
                assignments[key] = assign

                opt_iv = model.NewOptionalIntervalVar(
                    start, dur_var, end, assign, f"opt_{op.id}_{oper.id}"
                )
                opt_intervals[key] = opt_iv

        return CpsatVariables(
            op_start=op_start,
            op_end=op_end,
            op_interval=op_interval,
            op_duration=op_duration,
            assignments=assignments,
            operator_optional_intervals=opt_intervals,
        )

    # ── Step 6b — Assignment and shift constraints ─────────────────────────────

    def _add_assignment_constraints(self) -> None:
        """Require ≥ 1 operator per operation; scale duration for SIMULTANEOUS."""
        assert self.vars is not None
        v = self.vars
        model = self.model

        for op in self.operations:
            qualified = self._get_qualified_operators(op)
            residual  = v.op_duration[op.id]

            if not qualified:
                reason = (
                    f"L'operazione {op.id} (tipo {op.operation_type.value}) "
                    f"non ha operatori qualificati nel workcenter {op.workcenter_id}"
                )
                self._infeasibility_reasons.append(reason)
                # Force INFEASIBLE: start must exceed horizon (impossible).
                model.Add(v.op_start[op.id] > self.horizon)
                continue

            assign_vars = [v.assignments[(op.id, oper.id)] for oper in qualified]

            # At least one operator must be assigned.
            model.Add(sum(assign_vars) >= 1)

            if len(qualified) == 1:
                # Single eligible operator — fix duration to full residual.
                model.Add(self._dur_vars[op.id] == residual)
            else:
                # SIMULTANEOUS: effective_duration = floor(residual / n_assigned)
                n_assigned = model.NewIntVar(1, len(qualified), f"n_{op.id}")
                model.Add(n_assigned == sum(assign_vars))

                residual_const = model.NewConstant(residual)
                model.AddDivisionEquality(
                    self._dur_vars[op.id], residual_const, n_assigned
                )

    def _add_shift_nooverlap_constraints(self) -> None:
        from app.core.scheduler.shift_preprocessor import build_unavailable_intervals
        assert self.vars is not None
        v = self.vars
        model = self.model

        # Precomputa opt_ivs per operatore una sola volta
        from collections import defaultdict
        opt_ivs_by_oper: dict[uuid.UUID, list] = defaultdict(list)
        for (op_id, oper_id), iv in v.operator_optional_intervals.items():
            opt_ivs_by_oper[oper_id].append(iv)

        for oper in self.operators:
            opt_ivs = opt_ivs_by_oper.get(oper.id, [])

            if not oper.available_slots:
                for (op_id, oper_id), bv in v.assignments.items():
                    if oper_id == oper.id:
                        model.Add(bv == 0)
                continue

            unavailable = build_unavailable_intervals(
                operator_id=oper.id,
                all_slots=oper.available_slots,
                horizon_minutes=self.horizon,
                epoch=self.epoch,
            )

            fixed_ivs: list[cp_model.IntervalVar] = []
            for i, (s, e) in enumerate(unavailable):
                dur = e - s
                if dur < self.MIN_OP_DURATION:  # salta pause brevi
                    continue
                fiv = model.NewIntervalVar(s, dur, e, f"unavail_{oper.id}_{i}")
                fixed_ivs.append(fiv)

            all_ivs = fixed_ivs + opt_ivs
            if len(all_ivs) >= 2:
                model.AddNoOverlap(all_ivs)

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

    def _set_objective(self, objective_mode: str, params: dict) -> None:
        # TEMPORANEO: nessun obiettivo, solo soddisfacibilità
        pass
    
        # """Configure the CP-SAT optimisation objective."""
        # assert self.vars is not None
        # v = self.vars
        # model = self.model

        # if objective_mode == "FINISH_BY_DATE":
        #     if not v.op_end:
        #         return
        #     makespan = model.NewIntVar(0, self.horizon, "makespan")
        #     model.AddMaxEquality(makespan, list(v.op_end.values()))
        #     model.Minimize(makespan)
        #     # AM3 - TEMPORANEAMENTE COMMENTATO per debug poer vedere se il problema della non solzuione è il tempo ristretto
        #     # if "target_finish_minutes" in params:
        #     #     model.Add(makespan <= int(params["target_finish_minutes"]))

        # elif objective_mode == "MINIMIZE_OPERATORS":
        #     operator_ids = {oper_id for _, oper_id in v.assignments}
        #     used_vars: list[cp_model.BoolVar] = []
        #     for oper_id in operator_ids:
        #         used = model.NewBoolVar(f"oper_used_{oper_id}")
        #         oper_assigns = [
        #             bv for (op_id, oid), bv in v.assignments.items()
        #             if oid == oper_id
        #         ]
        #         if oper_assigns:
        #             model.AddMaxEquality(used, oper_assigns)
        #         else:
        #             model.Add(used == 0)
        #         used_vars.append(used)
        #     if used_vars:
        #         model.Minimize(sum(used_vars))

        # elif objective_mode == "MAXIMIZE_RESOURCE_UTILIZATION":
        #     # Maximise total planned work assigned (integer residuals as coefficients).
        #     terms = [
        #         assign_bv * v.op_duration.get(op_id, 0)
        #         for (op_id, oper_id), assign_bv in v.assignments.items()
        #     ]
        #     if terms:
        #         model.Maximize(sum(terms))

        # elif objective_mode == "CUSTOM":
        #     SCALE = 1000
        #     weights = params.get(
        #         "weights", {"makespan": 0.5, "operators": 0.3, "utilization": 0.2}
        #     )

        #     makespan = model.NewIntVar(0, self.horizon, "makespan_custom")
        #     if v.op_end:
        #         model.AddMaxEquality(makespan, list(v.op_end.values()))
        #     w_ms = int(weights.get("makespan", 0.5) * SCALE)

        #     operator_ids = {oper_id for _, oper_id in v.assignments}
        #     used_list: list[cp_model.BoolVar] = []
        #     for oper_id in operator_ids:
        #         used = model.NewBoolVar(f"oper_used_cust_{oper_id}")
        #         oper_assigns = [
        #             bv for (op_id, oid), bv in v.assignments.items()
        #             if oid == oper_id
        #         ]
        #         if oper_assigns:
        #             model.AddMaxEquality(used, oper_assigns)
        #         else:
        #             model.Add(used == 0)
        #         used_list.append(used)
        #     w_ops = int(weights.get("operators", 0.3) * SCALE)

        #     util_terms = [
        #         assign_bv * v.op_duration.get(op_id, 0)
        #         for (op_id, oper_id), assign_bv in v.assignments.items()
        #     ]
        #     max_util = max(1, sum(v.op_duration.values()) * max(1, len(self.operators)))
        #     total_util = model.NewIntVar(0, max_util, "total_util_cust")
        #     if util_terms:
        #         model.Add(total_util == sum(util_terms))
        #     w_util = int(weights.get("utilization", 0.2) * SCALE)

        #     obj = (
        #         w_ms * makespan
        #         + w_ops * (sum(used_list) if used_list else model.NewConstant(0))
        #         - w_util * total_util
        #     )
        #     model.Minimize(obj)

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
        self._add_missing_component_constraints()
        _log.info("Dopo precedence_constraints: %d constraints", len(self.model.Proto().constraints))  
        self._set_objective(objective_mode, params)

        self.solver.parameters.max_time_in_seconds = self.TIMEOUT
        self.solver.parameters.num_search_workers = min(8, max(1, os.cpu_count() or 1))
        # Permetti al solver di restituire la prima soluzione FEASIBLE trovata
        # senza cercare l'ottimo — molto più veloce per istanze grandi.
        self.solver.parameters.stop_after_first_solution = True

        
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

        

        status_code = self.solver.Solve(self.model)

        _log.info(
            "CP-SAT done in %.1fs: status=%s",
            self.solver.WallTime(),
            {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
             cp_model.INFEASIBLE: "INFEASIBLE", cp_model.UNKNOWN: "UNKNOWN"}.get(status_code, "?"),
        )

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

