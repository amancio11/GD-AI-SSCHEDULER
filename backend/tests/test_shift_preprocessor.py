"""Tests for shift_preprocessor.py.

All tests are in-memory — no DB required.
The DB-backed functions are tested with mock AsyncSession objects.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.scheduler.shift_preprocessor import (
    _shift_slots_for_day,
    build_unavailable_intervals,
    compute_epoch,
    compute_horizon_minutes,
    datetime_to_minutes,
    minutes_to_datetime,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


REF_DATE = date(2026, 1, 1)
EPOCH = compute_epoch(REF_DATE)  # 2026-01-01 00:00 UTC


# ─── compute_epoch ────────────────────────────────────────────────────────────

def test_compute_epoch_is_midnight_utc():
    d = date(2026, 3, 15)
    ep = compute_epoch(d)
    assert ep == datetime(2026, 3, 15, 0, 0, 0, tzinfo=timezone.utc)
    assert ep.tzinfo == timezone.utc


# ─── datetime_to_minutes / minutes_to_datetime ───────────────────────────────

def test_datetime_to_minutes_roundtrip():
    """Converting datetime → int → datetime must be lossless (floor semantics)."""
    dt = utc(2026, 1, 1, 6, 30)  # 390 minutes from epoch
    minutes = datetime_to_minutes(dt, EPOCH)
    assert minutes == 390
    recovered = minutes_to_datetime(minutes, EPOCH)
    assert recovered == dt


def test_datetime_to_minutes_floor():
    """Conversion must floor, not round."""
    # 6 hours, 30 minutes, 59 seconds — should still be 390, not 391
    epoch = compute_epoch(REF_DATE)
    dt = epoch + timedelta(hours=6, minutes=30, seconds=59)
    assert datetime_to_minutes(dt, epoch) == 390


def test_minutes_to_datetime_zero():
    assert minutes_to_datetime(0, EPOCH) == EPOCH


def test_datetime_to_minutes_negative():
    """A datetime before epoch returns a negative integer."""
    dt = EPOCH - timedelta(minutes=60)
    assert datetime_to_minutes(dt, EPOCH) == -60


# ─── compute_horizon_minutes ──────────────────────────────────────────────────

def test_compute_horizon_minutes():
    # end_date = same as reference_date → 23:59 = 1439 minutes
    horizon = compute_horizon_minutes(REF_DATE, EPOCH)
    assert horizon == 23 * 60 + 59


def test_compute_horizon_minutes_next_day():
    end = REF_DATE + timedelta(days=1)
    horizon = compute_horizon_minutes(end, EPOCH)
    assert horizon == 24 * 60 + 23 * 60 + 59  # 2879


# ─── _shift_slots_for_day ────────────────────────────────────────────────────

def test_morning_shift_two_slots():
    """06:00-14:00 with 30min break → slot1 06:00-10:00, slot2 10:30-14:00."""
    epoch = compute_epoch(date(2026, 1, 5))
    day = date(2026, 1, 5)
    slots = _shift_slots_for_day(
        day=day,
        shift_start_time=time(6, 0),
        shift_end_time=time(14, 0),
        break_duration_minutes=30,
        epoch=epoch,
    )
    assert len(slots) == 2
    # slot1: 06:00 → 10:00  = minutes 360 → 600 from epoch (06:00 = 360min, 10:00 = 600min)
    assert slots[0] == (360, 600)
    # slot2: 10:30 → 14:00 = minutes 630 → 840
    assert slots[1] == (630, 840)


def test_morning_shift_net_duration():
    """Net work time for an 8h shift with 30min break = 450 min."""
    epoch = compute_epoch(date(2026, 1, 5))
    day = date(2026, 1, 5)
    slots = _shift_slots_for_day(
        day=day,
        shift_start_time=time(6, 0),
        shift_end_time=time(14, 0),
        break_duration_minutes=30,
        epoch=epoch,
    )
    total_work = sum(end - start for start, end in slots)
    assert total_work == 450  # 480 - 30


def test_no_break_single_slot():
    """A shift with 0 min break returns exactly one slot."""
    epoch = compute_epoch(date(2026, 1, 5))
    day = date(2026, 1, 5)
    slots = _shift_slots_for_day(
        day=day,
        shift_start_time=time(8, 0),
        shift_end_time=time(16, 0),
        break_duration_minutes=0,
        epoch=epoch,
    )
    assert len(slots) == 1
    assert slots[0][1] - slots[0][0] == 480


def test_night_shift_crosses_midnight():
    """Notte 22:00-06:00 — end_date is the next calendar day."""
    epoch = compute_epoch(date(2026, 1, 5))
    day = date(2026, 1, 5)
    slots = _shift_slots_for_day(
        day=day,
        shift_start_time=time(22, 0),
        shift_end_time=time(6, 0),
        break_duration_minutes=30,
        epoch=epoch,
    )
    # shift starts at 2026-01-05 22:00 = day5*1440 + 22*60 = 7200 + 1320 = 8520min from epoch
    # epoch is 2026-01-05 00:00, so 22:00 = 22*60 = 1320 min from epoch
    assert len(slots) == 2
    s1_start, s1_end = slots[0]
    s2_start, s2_end = slots[1]
    # Total shift = 480 min, half = 240
    # slot1: 1320 → 1560, slot2: 1590 → 1800
    assert s1_start == 1320
    assert s1_end == 1560
    assert s2_start == 1590  # 1560 + 30
    assert s2_end == 1800    # 1320 + 480


def test_afternoon_shift_slots():
    """Pomeriggio 14:00-22:00, 30min break."""
    epoch = compute_epoch(date(2026, 1, 5))
    day = date(2026, 1, 5)
    slots = _shift_slots_for_day(
        day=day,
        shift_start_time=time(14, 0),
        shift_end_time=time(22, 0),
        break_duration_minutes=30,
        epoch=epoch,
    )
    assert len(slots) == 2
    # 14:00 = 14*60 = 840 min from epoch
    assert slots[0] == (840, 1080)   # 840 + 240 = 1080 → 18:00
    assert slots[1] == (1110, 1320)  # 1080 + 30 = 1110 → 18:30; 840 + 480 = 1320 → 22:00


# ─── build_unavailable_intervals ──────────────────────────────────────────────

def test_unavailable_intervals_contiguous():
    """Available only [100, 200] on horizon 300 → gaps are [(0,100), (200,300)]."""
    gaps = build_unavailable_intervals(
        operator_id=uuid.uuid4(),
        all_slots=[(100, 200)],
        horizon_minutes=300,
        epoch=EPOCH,
    )
    assert gaps == [(0, 100), (200, 300)]


def test_unavailable_all_day():
    """No available slots → one big unavailable interval [0, horizon]."""
    gaps = build_unavailable_intervals(
        operator_id=uuid.uuid4(),
        all_slots=[],
        horizon_minutes=480,
        epoch=EPOCH,
    )
    assert gaps == [(0, 480)]


def test_unavailable_nothing():
    """Slots span the full horizon → no unavailable intervals."""
    gaps = build_unavailable_intervals(
        operator_id=uuid.uuid4(),
        all_slots=[(0, 480)],
        horizon_minutes=480,
        epoch=EPOCH,
    )
    assert gaps == []


def test_unavailable_multiple_slots():
    """Three available slots → two gaps between them plus edges."""
    slots = [(0, 100), (200, 300), (400, 500)]
    gaps = build_unavailable_intervals(
        operator_id=uuid.uuid4(),
        all_slots=slots,
        horizon_minutes=600,
        epoch=EPOCH,
    )
    assert gaps == [(100, 200), (300, 400), (500, 600)]


def test_unavailable_zero_horizon():
    gaps = build_unavailable_intervals(
        operator_id=uuid.uuid4(),
        all_slots=[],
        horizon_minutes=0,
        epoch=EPOCH,
    )
    assert gaps == []


# ─── DB-backed functions (mocked) ────────────────────────────────────────────

def _make_cal_row(
    op_id: uuid.UUID,
    day: date,
    is_available: bool,
    shift_id,
    start_time,
    end_time,
    break_min: int,
):
    row = MagicMock()
    row.operator_id = op_id
    row.date = day
    row.is_available = is_available
    row.shift_id = shift_id
    row.start_time = start_time
    row.end_time = end_time
    row.break_duration_minutes = break_min
    return row


@pytest.mark.asyncio
async def test_full_shift_no_absence():
    """5 morning-shift days → 5 × 450 = 2250 min total available."""
    from app.core.scheduler.shift_preprocessor import build_operator_available_slots

    op_id = uuid.uuid4()
    sh_id = uuid.uuid4()
    start = date(2026, 1, 5)
    end = date(2026, 1, 9)
    epoch = compute_epoch(start)

    rows = [
        _make_cal_row(op_id, start + timedelta(days=i), True, sh_id,
                      time(6, 0), time(14, 0), 30)
        for i in range(5)
    ]

    async def fake_execute(stmt):
        result = MagicMock()
        result.all.return_value = rows
        return result

    db = AsyncMock()
    db.execute.side_effect = fake_execute

    slots = await build_operator_available_slots(op_id, start, end, epoch, db)
    total_work = sum(e - s for s, e in slots)
    assert total_work == 5 * 450  # 2250


@pytest.mark.asyncio
async def test_absence_day():
    """A day with is_available=False must produce no slots."""
    from app.core.scheduler.shift_preprocessor import build_operator_available_slots

    op_id = uuid.uuid4()
    sh_id = uuid.uuid4()
    start = date(2026, 1, 5)
    end = date(2026, 1, 5)
    epoch = compute_epoch(start)

    rows = [
        _make_cal_row(op_id, start, False, sh_id, time(6, 0), time(14, 0), 30)
    ]

    async def fake_execute(stmt):
        result = MagicMock()
        result.all.return_value = rows
        return result

    db = AsyncMock()
    db.execute.side_effect = fake_execute

    slots = await build_operator_available_slots(op_id, start, end, epoch, db)
    assert slots == []


@pytest.mark.asyncio
async def test_null_shift():
    """A calendar entry with shift_id=None (absence) must produce no slots."""
    from app.core.scheduler.shift_preprocessor import build_operator_available_slots

    op_id = uuid.uuid4()
    start = date(2026, 1, 5)
    end = date(2026, 1, 5)
    epoch = compute_epoch(start)

    rows = [
        _make_cal_row(op_id, start, True, None, None, None, 0)
    ]

    async def fake_execute(stmt):
        result = MagicMock()
        result.all.return_value = rows
        return result

    db = AsyncMock()
    db.execute.side_effect = fake_execute

    slots = await build_operator_available_slots(op_id, start, end, epoch, db)
    assert slots == []


@pytest.mark.asyncio
async def test_build_all_operators_slots_single_query():
    """build_all_operators_slots must issue exactly one DB query."""
    from app.core.scheduler.shift_preprocessor import build_all_operators_slots

    op1 = uuid.uuid4()
    op2 = uuid.uuid4()
    sh_id = uuid.uuid4()
    start = date(2026, 1, 5)
    end = date(2026, 1, 5)
    epoch = compute_epoch(start)

    rows = [
        _make_cal_row(op1, start, True, sh_id, time(6, 0), time(14, 0), 30),
        _make_cal_row(op2, start, True, sh_id, time(14, 0), time(22, 0), 30),
    ]

    async def fake_execute(stmt):
        result = MagicMock()
        result.all.return_value = rows
        return result

    db = AsyncMock()
    db.execute.side_effect = fake_execute

    result = await build_all_operators_slots([op1, op2], start, end, epoch, db)

    # Exactly one query
    assert db.execute.call_count == 1

    assert op1 in result
    assert op2 in result
    assert len(result[op1]) == 2  # 2 slots (morning shift with break)
    assert len(result[op2]) == 2  # 2 slots (afternoon shift with break)


@pytest.mark.asyncio
async def test_build_all_operators_missing_operator_gets_empty_list():
    """Operators with no calendar entries still appear with an empty list."""
    from app.core.scheduler.shift_preprocessor import build_all_operators_slots

    op1 = uuid.uuid4()
    op2 = uuid.uuid4()  # no calendar rows
    start = date(2026, 1, 5)
    end = date(2026, 1, 5)
    epoch = compute_epoch(start)

    async def fake_execute(stmt):
        result = MagicMock()
        result.all.return_value = []  # empty
        return result

    db = AsyncMock()
    db.execute.side_effect = fake_execute

    result = await build_all_operators_slots([op1, op2], start, end, epoch, db)
    assert result[op1] == []
    assert result[op2] == []
