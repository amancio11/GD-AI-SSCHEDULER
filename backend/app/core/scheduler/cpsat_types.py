"""CP-SAT type definitions — dataclasses shared across the scheduler pipeline.

These types are the contract between:
- shift_preprocessor   (produces QualifiedOperator.available_slots)
- cpsat_model_builder  (consumes all types, produces CpsatSolution)
- solution_extractor   (consumes CpsatVariables + CpsatSolution)

No business logic here — pure data containers.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from app.enums import OperationType, SkillType

if TYPE_CHECKING:
    # ortools types are only used as type hints at runtime inside the builder
    from ortools.sat.python.cp_model import BoolVar, IntVar, IntervalVar

    from app.schemas.schedule import ScheduleEntryCreate


@dataclass
class SchedulableOperation:
    """An operation that the CP-SAT model must schedule."""

    id: uuid.UUID
    routing_id: uuid.UUID
    production_order_id: uuid.UUID
    operation_type: OperationType          # ELECTRICAL | MECHANICAL | GENERAL
    workcenter_id: uuid.UUID
    planned_duration_minutes: int
    progress_pct: float                    # 0–100; residual = planned * (1 - pct/100)
    can_be_interrupted: bool
    earliest_start_minutes: int            # from missing-component or precedence constraints
    reference_point_id: uuid.UUID | None   # set only for machine-level operations


@dataclass
class QualifiedOperator:
    """An operator that is eligible to work on certain operations."""

    id: uuid.UUID
    skill: SkillType                       # ELECTRICAL | MECHANICAL | MULTI
    workcenter_id: uuid.UUID
    available_slots: list[tuple[int, int]] # [(start_min, end_min), ...] sorted ascending


@dataclass
class SegmentVars:
    """One work *segment* of an operation inside a single operator shift-slot.

    An operation is decomposed into N optional segments (one per candidate
    operator-slot).  Different segments of the same operation may belong to
    *different* operators and *different* shifts: this is what allows a long
    operation to be started by one operator in one shift and finished by another
    operator in a later shift (calendar-preemption / hand-off).

    The segment is "present" only if it carries work (``size > 0``).  When
    present, ``start``/``end`` are pinned inside ``[slot_start, slot_end]`` so the
    operator's shift calendar becomes a *hard* CP-SAT constraint — no post-solve
    correction is needed.
    """

    operator_id: uuid.UUID
    slot_start: int                # shift-slot lower bound (epoch minutes)
    slot_end: int                  # shift-slot upper bound (epoch minutes)
    present: BoolVar               # 1 ⟺ this segment carries work
    start: IntVar                  # work start inside the slot
    size: IntVar                   # work minutes done in this slot (0 if absent)
    end: IntVar                    # = start + size
    interval: IntervalVar          # optional interval, for NoOverlap


@dataclass
class CpsatVariables:
    """Container for all CP-SAT decision variables created for one model instance."""

    # Per-operation aggregate variables
    op_start: dict[uuid.UUID, IntVar]                    # op_id → first segment start (min)
    op_end:   dict[uuid.UUID, IntVar]                    # op_id → last segment end (max)
    op_duration: dict[uuid.UUID, int]                    # op_id → residual work duration

    # Reified "operator o does part of op" — (op_id, operator_id) → BoolVar.
    # Equals OR of the operator's segment-present vars; used by objectives.
    assignments: dict[tuple[uuid.UUID, uuid.UUID], BoolVar]

    # Per-operation work segments (the core of the model).
    segments: dict[uuid.UUID, list[SegmentVars]] = field(default_factory=dict)


@dataclass
class CpsatSolution:
    """Result returned by CpsatModelBuilder.build_and_solve()."""

    status: str                           # "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "UNKNOWN"
    schedule_entries: list[ScheduleEntryCreate] = field(default_factory=list)
    makespan_minutes: int | None = None
    operators_used: int | None = None
    solve_time_seconds: float = 0.0
    conflicts: list[str] = field(default_factory=list)  # populated when INFEASIBLE


# ── Skill ↔ OperationType compatibility table ─────────────────────────────────
# Used by _get_qualified_operators to decide which operators can handle an operation.
# MULTI operators can do everything.
_SKILL_CAN_DO: dict[SkillType, set[OperationType]] = {
    SkillType.ELECTRICAL: {OperationType.ELECTRICAL, OperationType.GENERAL},
    SkillType.MECHANICAL: {OperationType.MECHANICAL, OperationType.GENERAL},
    SkillType.MULTI:      {OperationType.ELECTRICAL, OperationType.MECHANICAL, OperationType.GENERAL},
}


def operator_can_do(operator: QualifiedOperator, op_type: OperationType) -> bool:
    """Return True if *operator*'s skill is compatible with *op_type*."""
    return op_type in _SKILL_CAN_DO.get(operator.skill, set())
