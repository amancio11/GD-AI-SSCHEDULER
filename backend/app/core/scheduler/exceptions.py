"""Custom exceptions for the scheduler core."""
from __future__ import annotations

import uuid


class CyclicDependencyError(Exception):
    """Raised when a cycle is detected in the reference-point precedence DAG."""

    def __init__(self, cycle_edges: list[tuple]) -> None:
        self.cycle_edges = cycle_edges
        nodes = [str(src) for src, _ in cycle_edges]
        # Close the cycle visually: repeat the first node at the end
        if cycle_edges:
            nodes.append(str(cycle_edges[0][0]))
        cycle_str = " → ".join(nodes)
        super().__init__(f"Ciclo rilevato nel DAG dei reference point: {cycle_str}")


class SchedulingInfeasibleError(Exception):
    """Raised when the CP-SAT model is provably infeasible."""

    def __init__(self, conflicts: list[str]) -> None:
        self.conflicts = conflicts
        detail = "; ".join(conflicts) if conflicts else "nessun dettaglio disponibile"
        super().__init__(f"Scheduling infeasible: {detail}")


class InsufficientResourcesError(Exception):
    """Raised when no qualified operator is available for an operation."""

    def __init__(
        self,
        operation_id: uuid.UUID,
        required_skill: str,
        workcenter: str,
    ) -> None:
        self.operation_id = operation_id
        self.required_skill = required_skill
        self.workcenter = workcenter
        super().__init__(
            f"Nessun operatore con skill {required_skill!r} disponibile "
            f"nel workcenter {workcenter!r} per l'operazione {operation_id}"
        )
