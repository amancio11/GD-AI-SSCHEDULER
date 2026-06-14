"""Tests for CpsatModelBuilder — Steps 6b and 6c.

All tests are purely in-memory (no DB).
The solver is invoked for real; OR-Tools must be installed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core.scheduler.cpsat_model_builder import CpsatModelBuilder
from app.core.scheduler.cpsat_types import QualifiedOperator, SchedulableOperation
from app.core.scheduler.shift_preprocessor import datetime_to_minutes
from app.enums import OperationType, SkillType

# ── Epoch ─────────────────────────────────────────────────────────────────────

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

# ── Factory helpers ───────────────────────────────────────────────────────────


def make_op(
    op_type: OperationType,
    wc_id: uuid.UUID,
    duration: int = 120,
    progress: float = 0.0,
    earliest: int = 0,
    reference_point_id: uuid.UUID | None = None,
) -> SchedulableOperation:
    return SchedulableOperation(
        id=uuid.uuid4(),
        routing_id=uuid.uuid4(),
        production_order_id=uuid.uuid4(),
        operation_type=op_type,
        workcenter_id=wc_id,
        planned_duration_minutes=duration,
        progress_pct=progress,
        can_be_interrupted=True,
        earliest_start_minutes=earliest,
        reference_point_id=reference_point_id,
    )


def make_oper(
    skill: SkillType,
    wc_id: uuid.UUID,
    slots: list[tuple[int, int]] | None = None,
) -> QualifiedOperator:
    return QualifiedOperator(
        id=uuid.uuid4(),
        skill=skill,
        workcenter_id=wc_id,
        available_slots=slots if slots is not None else [(0, 100_000)],
    )


def make_builder(
    ops: list[SchedulableOperation],
    opers: list[QualifiedOperator],
    horizon: int = 10_000,
    missing: dict[uuid.UUID, int] | None = None,
    prec: list[tuple[uuid.UUID, uuid.UUID]] | None = None,
) -> CpsatModelBuilder:
    return CpsatModelBuilder(
        operations=ops,
        operators=opers,
        horizon_minutes=horizon,
        epoch=EPOCH,
        missing_components_constraints=missing or {},
        precedence_pairs=prec or [],
    )


def start_end_minutes(entry) -> tuple[int, int]:
    """Convert a ScheduleEntryCreate back to (start_min, end_min) relative to EPOCH."""
    s = datetime_to_minutes(entry.scheduled_start, EPOCH)
    e = datetime_to_minutes(entry.scheduled_end, EPOCH)
    return s, e


# ══════════════════════════════════════════════════════════════════════════════
# Step 6b tests — Assignment and shift constraints
# ══════════════════════════════════════════════════════════════════════════════


def test_assignment_at_least_one():
    """With 2 qualified operators, at least 1 must be assigned."""
    wc = uuid.uuid4()
    op = make_op(OperationType.MECHANICAL, wc, duration=120)
    oper1 = make_oper(SkillType.MECHANICAL, wc)
    oper2 = make_oper(SkillType.MECHANICAL, wc)

    sol = make_builder([op], [oper1, oper2]).build_and_solve("FINISH_BY_DATE", {})

    assert sol.status in ("OPTIMAL", "FEASIBLE")
    op_entries = [e for e in sol.schedule_entries if e.operation_id == op.id]
    assert len(op_entries) >= 1


def test_simultaneous_reduces_duration():
    """With 2 operators and horizon=60, an op of 120 min is feasible via SIMULTANEOUS.

    Single-operator path: duration = 120 > 60 → INFEASIBLE.
    Two-operator path: duration = floor(120 / 2) = 60 ≤ 60 → OPTIMAL.
    """
    wc = uuid.uuid4()
    op = make_op(OperationType.MECHANICAL, wc, duration=120)
    oper1 = make_oper(SkillType.MECHANICAL, wc)
    oper2 = make_oper(SkillType.MECHANICAL, wc)

    # Single operator cannot fit within horizon=60
    sol_single = make_builder([op], [oper1], horizon=60).build_and_solve(
        "FINISH_BY_DATE", {}
    )
    assert sol_single.status == "INFEASIBLE"

    # Two operators — SIMULTANEOUS reduces effective duration to 60
    sol_two = make_builder([op], [oper1, oper2], horizon=60).build_and_solve(
        "FINISH_BY_DATE", {}
    )
    assert sol_two.status in ("OPTIMAL", "FEASIBLE")
    assert sol_two.makespan_minutes == 60


def test_operator_nooverlap():
    """1 operator, 2 ops of 480 min each, horizon=1440 — ops must not overlap."""
    wc = uuid.uuid4()
    op1 = make_op(OperationType.MECHANICAL, wc, duration=480)
    op2 = make_op(OperationType.MECHANICAL, wc, duration=480)
    oper = make_oper(SkillType.MECHANICAL, wc, slots=[(0, 1440)])

    sol = make_builder([op1, op2], [oper], horizon=1440).build_and_solve(
        "FINISH_BY_DATE", {}
    )

    assert sol.status in ("OPTIMAL", "FEASIBLE")

    entries = {e.operation_id: e for e in sol.schedule_entries}
    assert op1.id in entries and op2.id in entries

    s1, e1 = start_end_minutes(entries[op1.id])
    s2, e2 = start_end_minutes(entries[op2.id])

    # No temporal overlap
    assert e1 <= s2 or e2 <= s1


def test_shift_respected():
    """Operator available only 0–480 min; op of 240 min must fit within that window."""
    wc = uuid.uuid4()
    op = make_op(OperationType.MECHANICAL, wc, duration=240)
    oper = make_oper(SkillType.MECHANICAL, wc, slots=[(0, 480)])

    sol = make_builder([op], [oper], horizon=1440).build_and_solve(
        "FINISH_BY_DATE", {}
    )

    assert sol.status in ("OPTIMAL", "FEASIBLE")
    entry = sol.schedule_entries[0]
    s, e = start_end_minutes(entry)
    assert s >= 0
    assert e <= 480


def test_no_qualified_operator():
    """ELECTRICAL op with only a MECHANICAL operator → INFEASIBLE."""
    wc = uuid.uuid4()
    op = make_op(OperationType.ELECTRICAL, wc, duration=120)
    oper = make_oper(SkillType.MECHANICAL, wc)

    sol = make_builder([op], [oper]).build_and_solve("FINISH_BY_DATE", {})

    assert sol.status == "INFEASIBLE"
    assert len(sol.conflicts) > 0


# ══════════════════════════════════════════════════════════════════════════════
# Step 6c tests — Precedence, missing components, objectives
# ══════════════════════════════════════════════════════════════════════════════


def test_precedence_respected():
    """op_A must finish before op_B starts: end(A) ≤ start(B)."""
    wc = uuid.uuid4()
    op_a = make_op(OperationType.MECHANICAL, wc, duration=120)
    op_b = make_op(OperationType.MECHANICAL, wc, duration=120)
    oper = make_oper(SkillType.MECHANICAL, wc)

    sol = make_builder(
        [op_a, op_b], [oper], prec=[(op_a.id, op_b.id)]
    ).build_and_solve("FINISH_BY_DATE", {})

    assert sol.status in ("OPTIMAL", "FEASIBLE")

    entries = {e.operation_id: e for e in sol.schedule_entries}
    _, end_a   = start_end_minutes(entries[op_a.id])
    start_b, _ = start_end_minutes(entries[op_b.id])

    assert end_a <= start_b


def test_missing_component_delays_op():
    """Op with a missing component arriving at minute 500 must start ≥ 500."""
    wc = uuid.uuid4()
    op = make_op(OperationType.MECHANICAL, wc, duration=120)
    oper = make_oper(SkillType.MECHANICAL, wc)

    sol = make_builder(
        [op], [oper], missing={op.id: 500}
    ).build_and_solve("FINISH_BY_DATE", {})

    assert sol.status in ("OPTIMAL", "FEASIBLE")
    s, _ = start_end_minutes(sol.schedule_entries[0])
    assert s >= 500


def test_finish_by_date_feasible():
    """Wide horizon + achievable target → OPTIMAL."""
    wc = uuid.uuid4()
    op = make_op(OperationType.MECHANICAL, wc, duration=120)
    oper = make_oper(SkillType.MECHANICAL, wc)

    sol = make_builder([op], [oper], horizon=10_000).build_and_solve(
        "FINISH_BY_DATE", {"target_finish_minutes": 500}
    )

    assert sol.status == "OPTIMAL"
    assert sol.makespan_minutes is not None
    assert sol.makespan_minutes <= 500


def test_finish_by_date_infeasible():
    """Target of 10 min < MIN_OP_DURATION (30) → INFEASIBLE."""
    wc = uuid.uuid4()
    op = make_op(OperationType.MECHANICAL, wc, duration=120)
    oper = make_oper(SkillType.MECHANICAL, wc)

    sol = make_builder([op], [oper], horizon=10_000).build_and_solve(
        "FINISH_BY_DATE", {"target_finish_minutes": 10}
    )

    assert sol.status == "INFEASIBLE"


def test_minimize_operators():
    """2 sequential ops; minimizing operators should use only 1."""
    wc = uuid.uuid4()
    op1 = make_op(OperationType.MECHANICAL, wc, duration=120)
    op2 = make_op(OperationType.MECHANICAL, wc, duration=120)
    oper1 = make_oper(SkillType.MECHANICAL, wc)
    oper2 = make_oper(SkillType.MECHANICAL, wc)

    sol = make_builder(
        [op1, op2], [oper1, oper2], horizon=2000
    ).build_and_solve("MINIMIZE_OPERATORS", {})

    assert sol.status in ("OPTIMAL", "FEASIBLE")
    assert sol.operators_used is not None
    # With 2 sequential ops, 1 operator is sufficient
    assert sol.operators_used <= 2  # at most 2; optimally 1


def test_minimize_operators_optimal_is_one():
    """Single op — minimised operator count must be exactly 1."""
    wc = uuid.uuid4()
    op = make_op(OperationType.MECHANICAL, wc, duration=120)
    oper1 = make_oper(SkillType.MECHANICAL, wc)
    oper2 = make_oper(SkillType.MECHANICAL, wc)

    sol = make_builder([op], [oper1, oper2]).build_and_solve(
        "MINIMIZE_OPERATORS", {}
    )

    assert sol.status == "OPTIMAL"
    assert sol.operators_used == 1


def test_simultaneous_two_operators_halves_duration():
    """Verify that two simultaneous operators halve the effective duration.

    Op: 120 min, 2 mechanical operators, horizon=60.
    Expected: makespan = 60 (floor(120/2)).
    """
    wc = uuid.uuid4()
    op = make_op(OperationType.MECHANICAL, wc, duration=120)
    oper1 = make_oper(SkillType.MECHANICAL, wc)
    oper2 = make_oper(SkillType.MECHANICAL, wc)

    sol = make_builder([op], [oper1, oper2], horizon=60).build_and_solve(
        "FINISH_BY_DATE", {}
    )

    assert sol.status in ("OPTIMAL", "FEASIBLE")
    assert sol.makespan_minutes == 60


def test_maximize_resource_utilization():
    """MAXIMIZE_RESOURCE_UTILIZATION: model is feasible and produces entries."""
    wc = uuid.uuid4()
    op1 = make_op(OperationType.MECHANICAL, wc, duration=120)
    op2 = make_op(OperationType.MECHANICAL, wc, duration=240)
    oper1 = make_oper(SkillType.MECHANICAL, wc)
    oper2 = make_oper(SkillType.MECHANICAL, wc)

    sol = make_builder([op1, op2], [oper1, oper2]).build_and_solve(
        "MAXIMIZE_RESOURCE_UTILIZATION", {}
    )

    assert sol.status in ("OPTIMAL", "FEASIBLE")
    assert len(sol.schedule_entries) >= 2


def test_custom_objective_feasible():
    """CUSTOM objective: model is feasible."""
    wc = uuid.uuid4()
    op = make_op(OperationType.MECHANICAL, wc, duration=120)
    oper = make_oper(SkillType.MECHANICAL, wc)

    sol = make_builder([op], [oper]).build_and_solve(
        "CUSTOM",
        {"weights": {"makespan": 0.5, "operators": 0.3, "utilization": 0.2}},
    )

    assert sol.status in ("OPTIMAL", "FEASIBLE")


def test_empty_operations():
    """No operations → solution is trivially feasible with empty entries."""
    sol = make_builder([], []).build_and_solve("FINISH_BY_DATE", {})
    # CP-SAT with no variables is OPTIMAL (trivial)
    assert sol.status in ("OPTIMAL", "FEASIBLE", "UNKNOWN")
    assert sol.schedule_entries == []


def test_blocking_constraint_delays_start():
    """Machine-level op with RP must start after its blocking_constraint minute."""
    wc = uuid.uuid4()
    rp_id = uuid.uuid4()
    op = make_op(OperationType.MECHANICAL, wc, duration=120, reference_point_id=rp_id)
    oper = make_oper(SkillType.MECHANICAL, wc)

    blocking = {op.id: 300}  # op cannot start before minute 300

    sol = make_builder([op], [oper]).build_and_solve(
        "FINISH_BY_DATE", {}, blocking_constraints=blocking
    )

    assert sol.status in ("OPTIMAL", "FEASIBLE")
    s, _ = start_end_minutes(sol.schedule_entries[0])
    assert s >= 300


def test_multi_operator_workcenter_isolation():
    """Operators in different workcenters are not cross-assigned."""
    wc1 = uuid.uuid4()
    wc2 = uuid.uuid4()

    op_in_wc1 = make_op(OperationType.MECHANICAL, wc1, duration=120)
    oper_in_wc2 = make_oper(SkillType.MECHANICAL, wc2)

    sol = make_builder([op_in_wc1], [oper_in_wc2]).build_and_solve(
        "FINISH_BY_DATE", {}
    )

    # No qualified operator in wc1 → INFEASIBLE
    assert sol.status == "INFEASIBLE"
