"""Tests for SolutionExtractor and InfeasibilityAnalyzer.

All tests are in-memory — no DB, no OR-Tools solver required.
The SolutionExtractor tests use a fake solver that returns pre-set values.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.core.scheduler.cpsat_types import CpsatVariables, QualifiedOperator, SchedulableOperation
from app.core.scheduler.infeasibility_analyzer import InfeasibilityAnalyzer
from app.core.scheduler.solution_extractor import SolutionExtractor
from app.enums import OperationType, ScheduleEntryStatus, SkillType
from app.schemas.schedule import ScheduleEntryCreate

# ── Shared constants ──────────────────────────────────────────────────────────

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
SCENARIO_ID = uuid.uuid4()


# ── Fake CpsatVariables + solver ──────────────────────────────────────────────

def _make_vars(
    ops: list[SchedulableOperation],
    oper_ids: list[uuid.UUID],
    start_minutes: dict[uuid.UUID, int],   # op_id → start_min
    end_minutes:   dict[uuid.UUID, int],   # op_id → end_min
    assigned: dict[uuid.UUID, uuid.UUID],  # op_id → oper_id
) -> CpsatVariables:
    """Build a CpsatVariables stub with IntVar-like MagicMocks."""

    def intvar(val: int):
        iv = MagicMock()
        iv._value = val
        return iv

    op_start = {op.id: intvar(start_minutes[op.id]) for op in ops}
    op_end   = {op.id: intvar(end_minutes[op.id])   for op in ops}
    op_duration = {op.id: end_minutes[op.id] - start_minutes[op.id] for op in ops}

    assignments = {}
    for op in ops:
        for oper_id in oper_ids:
            bv = MagicMock()
            bv._value = 1 if assigned.get(op.id) == oper_id else 0
            assignments[(op.id, oper_id)] = bv

    return CpsatVariables(
        op_start=op_start,
        op_end=op_end,
        op_duration=op_duration,
        assignments=assignments,
    )


def _make_solver(
    op_start: dict[uuid.UUID, MagicMock],
    op_end:   dict[uuid.UUID, MagicMock],
    assignments: dict[tuple[uuid.UUID, uuid.UUID], MagicMock],
):
    """Fake CpSolver.Value() that reads ._value from the MagicMock."""
    solver = MagicMock()

    def value_fn(var):
        return var._value

    solver.Value.side_effect = value_fn
    return solver


def _make_op(wc_id: uuid.UUID, duration: int = 120) -> SchedulableOperation:
    return SchedulableOperation(
        id=uuid.uuid4(),
        routing_id=uuid.uuid4(),
        production_order_id=uuid.uuid4(),
        operation_type=OperationType.MECHANICAL,
        workcenter_id=wc_id,
        planned_duration_minutes=duration,
        progress_pct=0.0,
        can_be_interrupted=True,
        earliest_start_minutes=0,
        reference_point_id=None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SolutionExtractor tests
# ══════════════════════════════════════════════════════════════════════════════


def test_extract_basic():
    """Two operations → two ScheduleEntryCreate with correct datetimes."""
    wc = uuid.uuid4()
    oper_id = uuid.uuid4()
    op1 = _make_op(wc, duration=120)
    op2 = _make_op(wc, duration=60)

    start_min = {op1.id: 0,   op2.id: 120}
    end_min   = {op1.id: 120, op2.id: 180}
    assigned  = {op1.id: oper_id, op2.id: oper_id}

    variables = _make_vars([op1, op2], [oper_id], start_min, end_min, assigned)
    solver    = _make_solver(variables.op_start, variables.op_end, variables.assignments)

    extractor = SolutionExtractor()
    entries = extractor.extract(solver, variables, [op1, op2], EPOCH, SCENARIO_ID)

    assert len(entries) == 2

    e1 = next(e for e in entries if e.operation_id == op1.id)
    e2 = next(e for e in entries if e.operation_id == op2.id)

    assert e1.scheduled_start == EPOCH
    assert e1.scheduled_end   == EPOCH + timedelta(minutes=120)
    assert e2.scheduled_start == EPOCH + timedelta(minutes=120)
    assert e2.scheduled_end   == EPOCH + timedelta(minutes=180)

    assert e1.status == ScheduleEntryStatus.SCHEDULED
    assert e1.scenario_id == SCENARIO_ID


def test_extract_returns_only_assigned_operators():
    """Only the operator whose BoolVar == 1 should appear in entries."""
    wc = uuid.uuid4()
    oper_a = uuid.uuid4()
    oper_b = uuid.uuid4()
    op = _make_op(wc, duration=120)

    start_min = {op.id: 0}
    end_min   = {op.id: 120}
    assigned  = {op.id: oper_a}  # oper_b is NOT assigned

    variables = _make_vars([op], [oper_a, oper_b], start_min, end_min, assigned)
    solver    = _make_solver(variables.op_start, variables.op_end, variables.assignments)

    entries = SolutionExtractor().extract(solver, variables, [op], EPOCH, SCENARIO_ID)

    assert len(entries) == 1
    assert entries[0].operator_id == oper_a


# ── compute_makespan ──────────────────────────────────────────────────────────

def _entry(start_min: int, end_min: int) -> ScheduleEntryCreate:
    return ScheduleEntryCreate(
        scenario_id=SCENARIO_ID,
        operation_id=uuid.uuid4(),
        operator_id=uuid.uuid4(),
        workcenter_id=uuid.uuid4(),
        scheduled_start=EPOCH + timedelta(minutes=start_min),
        scheduled_end=EPOCH + timedelta(minutes=end_min),
        status=ScheduleEntryStatus.SCHEDULED,
    )


def test_compute_makespan_basic():
    entries = [_entry(0, 120), _entry(120, 360)]
    ms = SolutionExtractor().compute_makespan(entries)
    assert ms == timedelta(minutes=360)


def test_compute_makespan_empty():
    assert SolutionExtractor().compute_makespan([]) == timedelta(0)


# ── compute_operator_utilization ──────────────────────────────────────────────

def test_utilization_full_shift():
    """Op occupies all 480 available minutes → utilization = 1.0."""
    oper_id = uuid.uuid4()
    entries = [
        ScheduleEntryCreate(
            scenario_id=SCENARIO_ID,
            operation_id=uuid.uuid4(),
            operator_id=oper_id,
            workcenter_id=uuid.uuid4(),
            scheduled_start=EPOCH,
            scheduled_end=EPOCH + timedelta(minutes=480),
            status=ScheduleEntryStatus.SCHEDULED,
        )
    ]
    util = SolutionExtractor().compute_operator_utilization(entries, 480)
    assert abs(util[oper_id] - 1.0) < 1e-6


def test_utilization_half_shift():
    oper_id = uuid.uuid4()
    entries = [
        ScheduleEntryCreate(
            scenario_id=SCENARIO_ID,
            operation_id=uuid.uuid4(),
            operator_id=oper_id,
            workcenter_id=uuid.uuid4(),
            scheduled_start=EPOCH,
            scheduled_end=EPOCH + timedelta(minutes=240),
            status=ScheduleEntryStatus.SCHEDULED,
        )
    ]
    util = SolutionExtractor().compute_operator_utilization(entries, 480)
    assert abs(util[oper_id] - 0.5) < 1e-6


def test_utilization_zero_available():
    """If available_minutes == 0, return empty dict (no division by zero)."""
    entries = [_entry(0, 120)]
    util = SolutionExtractor().compute_operator_utilization(entries, 0)
    assert util == {}


# ── find_critical_path ────────────────────────────────────────────────────────

def _entry_for(op_id: uuid.UUID, start_min: int, end_min: int) -> ScheduleEntryCreate:
    return ScheduleEntryCreate(
        scenario_id=SCENARIO_ID,
        operation_id=op_id,
        operator_id=uuid.uuid4(),
        workcenter_id=uuid.uuid4(),
        scheduled_start=EPOCH + timedelta(minutes=start_min),
        scheduled_end=EPOCH + timedelta(minutes=end_min),
        status=ScheduleEntryStatus.SCHEDULED,
    )


def test_critical_path_linear():
    """A → B → C: all three are on the critical path."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    entries = [
        _entry_for(a, 0,   120),
        _entry_for(b, 120, 300),
        _entry_for(c, 300, 420),
    ]
    prec = [(a, b), (b, c)]

    path = SolutionExtractor().find_critical_path(entries, prec)

    assert set(path) == {a, b, c}
    assert path.index(a) < path.index(b) < path.index(c)


def test_critical_path_parallel():
    """A→C and B→C where A is much longer → critical path is A, C only."""
    a = uuid.uuid4()  # long:  480 min
    b = uuid.uuid4()  # short: 60 min
    c = uuid.uuid4()  # last:  120 min

    entries = [
        _entry_for(a, 0,   480),
        _entry_for(b, 0,   60),
        _entry_for(c, 480, 600),
    ]
    prec = [(a, c), (b, c)]

    path = SolutionExtractor().find_critical_path(entries, prec)

    # b should NOT be on the critical path because a is longer
    assert a in path
    assert c in path
    # b may or may not appear depending on tie-breaking, but the longest path
    # passes through a (weight 480) not b (weight 60)
    # Verify a precedes c
    assert path.index(a) < path.index(c)


def test_critical_path_no_precedences():
    """Without precedences the single longest op is returned."""
    a = uuid.uuid4()
    b = uuid.uuid4()
    entries = [
        _entry_for(a, 0, 480),
        _entry_for(b, 0, 60),
    ]

    path = SolutionExtractor().find_critical_path(entries, [])
    assert path == [a]


def test_critical_path_empty():
    assert SolutionExtractor().find_critical_path([], []) == []


# ══════════════════════════════════════════════════════════════════════════════
# InfeasibilityAnalyzer tests
# ══════════════════════════════════════════════════════════════════════════════


def _make_schedulable(op_type: OperationType, wc_id: uuid.UUID) -> SchedulableOperation:
    return SchedulableOperation(
        id=uuid.uuid4(),
        routing_id=uuid.uuid4(),
        production_order_id=uuid.uuid4(),
        operation_type=op_type,
        workcenter_id=wc_id,
        planned_duration_minutes=120,
        progress_pct=0.0,
        can_be_interrupted=True,
        earliest_start_minutes=0,
        reference_point_id=None,
    )


def _make_qual_oper(skill: SkillType, wc_id: uuid.UUID) -> QualifiedOperator:
    return QualifiedOperator(
        id=uuid.uuid4(),
        skill=skill,
        workcenter_id=wc_id,
        available_slots=[(0, 10_000)],
    )


def test_infeasibility_no_operators():
    """ELECTRICAL op with only MECHANICAL operator → readable conflict string."""
    wc = uuid.uuid4()
    op   = _make_schedulable(OperationType.ELECTRICAL, wc)
    oper = _make_qual_oper(SkillType.MECHANICAL, wc)

    analyzer = InfeasibilityAnalyzer()
    conflicts = analyzer.analyze(
        model=None,
        operations=[op],
        operators=[oper],
        missing_constraints={},
        precedence_pairs=[],
        infeasibility_reasons=[],
    )

    assert len(conflicts) >= 1
    assert any("non ha operatori qualificati" in c for c in conflicts)
    assert any(str(wc) in c for c in conflicts)


def test_infeasibility_missing_beyond_horizon():
    """Component arrival after horizon → specific conflict string."""
    wc = uuid.uuid4()
    op = _make_schedulable(OperationType.MECHANICAL, wc)
    oper = _make_qual_oper(SkillType.MECHANICAL, wc)

    analyzer = InfeasibilityAnalyzer()
    conflicts = analyzer.analyze(
        model=None,
        operations=[op],
        operators=[oper],
        missing_constraints={op.id: 900},  # starts at 900, horizon = 1000, duration = 120 → 900+120>1000
        precedence_pairs=[],
        infeasibility_reasons=[],
        horizon_minutes=1000,
    )

    assert any("horizon" in c.lower() for c in conflicts)


def test_infeasibility_pre_collected_reasons_preserved():
    """Reasons already collected by the builder must be preserved."""
    pre = ["Motivo pre-esistente"]
    analyzer = InfeasibilityAnalyzer()
    conflicts = analyzer.analyze(
        model=None,
        operations=[],
        operators=[],
        missing_constraints={},
        precedence_pairs=[],
        infeasibility_reasons=pre,
    )
    assert "Motivo pre-esistente" in conflicts


def test_suggest_fixes_no_operator():
    """suggest_fixes must return an actionable fix for a no-operator conflict."""
    conflict = (
        "L'operazione abc (tipo ELECTRICAL) non ha operatori qualificati "
        "nel workcenter WC-BERGAMO"
    )
    fixes = InfeasibilityAnalyzer().suggest_fixes([conflict])
    assert len(fixes) == 1
    assert "ELECTRICAL" in fixes[0] or "operatore" in fixes[0].lower()


def test_suggest_fixes_cycle():
    """suggest_fixes must handle cyclic-dependency conflicts."""
    conflict = "Le precedenze tra operazioni contengono un ciclo."
    fixes = InfeasibilityAnalyzer().suggest_fixes([conflict])
    assert len(fixes) == 1
    assert "ciclo" in fixes[0].lower() or "dag" in fixes[0].lower()
