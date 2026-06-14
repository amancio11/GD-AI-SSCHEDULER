"""Solution Extractor — converts a CP-SAT solution into ScheduleEntryCreate objects.

Also provides:
  - compute_makespan     → timedelta between first start and last end
  - compute_operator_utilization → utilisation ratio per operator
  - find_critical_path   → longest-path on the precedence DAG weighted by durations
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import timedelta

import networkx as nx

from app.core.scheduler.cpsat_types import CpsatVariables, SchedulableOperation
from app.core.scheduler.shift_preprocessor import minutes_to_datetime
from app.enums import ScheduleEntryStatus
from app.schemas.schedule import ScheduleEntryCreate

from ortools.sat.python import cp_model


class SolutionExtractor:
    """Extracts structured data from a solved CP-SAT model."""

    def extract(
        self,
        solver: cp_model.CpSolver,
        variables: CpsatVariables,
        operations: list[SchedulableOperation],
        epoch,               # datetime (UTC)
        scenario_id: uuid.UUID,
    ) -> list[ScheduleEntryCreate]:
        """Build one ScheduleEntryCreate per (operation, assigned operator) pair.

        For each operation the method:
          1. Reads solver.Value(op_start[op.id]) and op_end[op.id].
          2. Finds the operator(s) with assign[(op.id, oper.id)] == 1.
          3. Converts integer minutes → UTC datetime via minutes_to_datetime.
          4. Returns a list of ScheduleEntryCreate ready to be persisted.
        """
        entries: list[ScheduleEntryCreate] = []

        for op in operations:
            if op.id not in variables.op_start:
                continue

            start_min = solver.Value(variables.op_start[op.id])
            end_min   = solver.Value(variables.op_end[op.id])
            s_start   = minutes_to_datetime(start_min, epoch)
            s_end     = minutes_to_datetime(end_min, epoch)

            for (op_id, oper_id), bv in variables.assignments.items():
                if op_id != op.id:
                    continue
                if solver.Value(bv) != 1:
                    continue

                entries.append(
                    ScheduleEntryCreate(
                        scenario_id=scenario_id,
                        operation_id=op.id,
                        operator_id=oper_id,
                        workcenter_id=op.workcenter_id,
                        scheduled_start=s_start,
                        scheduled_end=s_end,
                        status=ScheduleEntryStatus.SCHEDULED,
                    )
                )

        return entries

    def compute_makespan(self, entries: list[ScheduleEntryCreate]) -> timedelta:
        """Return max(scheduled_end) − min(scheduled_start) across all entries."""
        if not entries:
            return timedelta(0)
        min_start = min(e.scheduled_start for e in entries)
        max_end   = max(e.scheduled_end   for e in entries)
        return max_end - min_start

    def compute_operator_utilization(
        self,
        entries: list[ScheduleEntryCreate],
        total_available_minutes: int,
    ) -> dict[uuid.UUID, float]:
        """Return utilisation ratio per operator: minutes_worked / total_available.

        Args:
            entries: The schedule entries for this scenario.
            total_available_minutes: The total minutes each operator had available
                (same for all operators; caller provides the value).

        Returns:
            {operator_id: ratio}  where ratio ∈ [0.0, 1.0].
        """
        if total_available_minutes <= 0:
            return {}

        worked: dict[uuid.UUID, int] = defaultdict(int)
        for e in entries:
            duration = int((e.scheduled_end - e.scheduled_start).total_seconds() // 60)
            worked[e.operator_id] += duration

        return {
            op_id: min(1.0, mins / total_available_minutes)
            for op_id, mins in worked.items()
        }

    def find_critical_path(
        self,
        entries: list[ScheduleEntryCreate],
        precedence_pairs: list[tuple[uuid.UUID, uuid.UUID]],
    ) -> list[uuid.UUID]:
        """Return the critical-path operation IDs (longest path in the DAG).

        Algorithm:
          1. Build a directed weighted graph: operation_id → operation_id.
             Edge weight = duration of the *predecessor* operation (in minutes).
          2. Compute the longest path (max weight path) using
             networkx.dag_longest_path on the negated-weight graph
             (actually we use dag_longest_path with weight='duration').
          3. Return the nodes on that path in topological order.

        If the precedence DAG is empty (no pairs), every operation with
        maximum duration is returned as a single-node critical path.
        """
        if not entries:
            return []

        # Index entries by operation_id for O(1) lookup.
        entry_by_op: dict[uuid.UUID, ScheduleEntryCreate] = {}
        for e in entries:
            # Keep the one with the latest end (multi-operator same op → same times)
            if e.operation_id not in entry_by_op:
                entry_by_op[e.operation_id] = e

        def duration_min(op_id: uuid.UUID) -> int:
            e = entry_by_op.get(op_id)
            if e is None:
                return 0
            return int((e.scheduled_end - e.scheduled_start).total_seconds() // 60)

        dag: nx.DiGraph = nx.DiGraph()

        for op_id in entry_by_op:
            dag.add_node(op_id)

        for pred_id, succ_id in precedence_pairs:
            if pred_id in entry_by_op and succ_id in entry_by_op:
                dag.add_edge(pred_id, succ_id, duration=duration_min(pred_id))

        if not nx.is_directed_acyclic_graph(dag):
            # Safeguard: if a cycle slipped through, return all ops.
            return list(entry_by_op.keys())

        if dag.number_of_edges() == 0:
            # No precedences — critical path is the single longest operation.
            if not entry_by_op:
                return []
            max_id = max(entry_by_op, key=lambda oid: duration_min(oid))
            return [max_id]

        path = nx.dag_longest_path(dag, weight="duration")
        return path
