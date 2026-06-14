"""Shift Preprocessor — CP-SAT pre-processing phase.

Converts operator_calendar + shifts into integer minute slots relative to an
epoch so that CP-SAT can work entirely with integers.

Epoch = reference_date at 00:00 UTC (minute 0).
All arithmetic uses integer floor — never round.

Break model: the break is placed at the mid-point of the *total* shift duration.
  Example: shift 06:00-14:00 (480 min), break 30 min →
    slot1: 06:00-10:00  (240 min)
    break: 10:00-10:30
    slot2: 10:30-14:00  (210 min)
  Net work = 480 - 30 = 450 min ✓
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    pass


# ─── Pure conversion helpers ──────────────────────────────────────────────────

def compute_epoch(reference_date: date) -> datetime:
    """Return *reference_date* at 00:00:00 UTC — the CP-SAT minute-0 anchor."""
    return datetime(
        reference_date.year,
        reference_date.month,
        reference_date.day,
        0, 0, 0,
        tzinfo=timezone.utc,
    )


def datetime_to_minutes(dt: datetime, epoch: datetime) -> int:
    """Convert *dt* to integer minutes from *epoch* (floor, never round).

    Both *dt* and *epoch* must be timezone-aware (UTC).
    """
    delta = dt.astimezone(timezone.utc) - epoch.astimezone(timezone.utc)
    return int(delta.total_seconds() // 60)


def minutes_to_datetime(minutes: int, epoch: datetime) -> datetime:
    """Inverse of datetime_to_minutes — return a timezone-aware UTC datetime."""
    return epoch.astimezone(timezone.utc) + timedelta(minutes=minutes)


def compute_horizon_minutes(end_date: date, epoch: datetime) -> int:
    """Return minutes from *epoch* to *end_date* at 23:59 UTC."""
    end_dt = datetime(
        end_date.year, end_date.month, end_date.day,
        23, 59, 0,
        tzinfo=timezone.utc,
    )
    return datetime_to_minutes(end_dt, epoch)


# ─── Slot computation (pure, no DB) ──────────────────────────────────────────

def _shift_slots_for_day(
    day: date,
    shift_start_time,   # datetime.time (UTC-based)
    shift_end_time,     # datetime.time (UTC-based)
    break_duration_minutes: int,
    epoch: datetime,
) -> list[tuple[int, int]]:
    """Compute the two available slots for a single calendar day.

    Returns a list of (start_minute, end_minute) pairs (relative to epoch).
    If break_duration_minutes == 0 a single slot is returned.
    """
    shift_start_dt = datetime(
        day.year, day.month, day.day,
        shift_start_time.hour, shift_start_time.minute,
        tzinfo=timezone.utc,
    )

    # Night shift crosses midnight: end_time < start_time
    if shift_end_time > shift_start_time:
        shift_end_dt = datetime(
            day.year, day.month, day.day,
            shift_end_time.hour, shift_end_time.minute,
            tzinfo=timezone.utc,
        )
    else:
        # Shift ends on the following day
        next_day = day + timedelta(days=1)
        shift_end_dt = datetime(
            next_day.year, next_day.month, next_day.day,
            shift_end_time.hour, shift_end_time.minute,
            tzinfo=timezone.utc,
        )

    total_minutes = int((shift_end_dt - shift_start_dt).total_seconds() // 60)
    shift_start_min = datetime_to_minutes(shift_start_dt, epoch)

    if break_duration_minutes <= 0:
        return [(shift_start_min, shift_start_min + total_minutes)]

    # Break at the mid-point of the total shift
    half_point = total_minutes // 2  # floor

    slot1_start = shift_start_min
    slot1_end = shift_start_min + half_point

    slot2_start = slot1_end + break_duration_minutes
    slot2_end = shift_start_min + total_minutes

    slots: list[tuple[int, int]] = []
    if slot1_end > slot1_start:
        slots.append((slot1_start, slot1_end))
    if slot2_end > slot2_start:
        slots.append((slot2_start, slot2_end))
    return slots


# ─── DB-backed slot builders ──────────────────────────────────────────────────

async def build_operator_available_slots(
    operator_id: uuid.UUID,
    start_date: date,
    end_date: date,
    epoch: datetime,
    db: AsyncSession,
) -> list[tuple[int, int]]:
    """Build available time slots for *operator_id* in [start_date, end_date].

    Returns a chronologically-sorted list of (start_min, end_min) pairs.
    Days with is_available=False or shift_id=None are skipped.
    """
    from app.models.operator import OperatorCalendar, Shift  # local import avoids circular

    rows = (
        await db.execute(
            select(
                OperatorCalendar.date,
                OperatorCalendar.is_available,
                OperatorCalendar.shift_id,
                Shift.start_time,
                Shift.end_time,
                Shift.break_duration_minutes,
            )
            .outerjoin(Shift, OperatorCalendar.shift_id == Shift.id)
            .where(
                and_(
                    OperatorCalendar.operator_id == operator_id,
                    OperatorCalendar.date >= start_date,
                    OperatorCalendar.date <= end_date,
                )
            )
            .order_by(OperatorCalendar.date)
        )
    ).all()

    slots: list[tuple[int, int]] = []
    for row in rows:
        if not row.is_available or row.shift_id is None or row.start_time is None:
            continue
        day_slots = _shift_slots_for_day(
            day=row.date,
            shift_start_time=row.start_time,
            shift_end_time=row.end_time,
            break_duration_minutes=row.break_duration_minutes,
            epoch=epoch,
        )
        slots.extend(day_slots)

    return slots


async def build_all_operators_slots(
    operator_ids: list[uuid.UUID],
    start_date: date,
    end_date: date,
    epoch: datetime,
    db: AsyncSession,
) -> dict[uuid.UUID, list[tuple[int, int]]]:
    """Build available slots for multiple operators in a *single* DB query.

    Returns ``{operator_id: [(start_min, end_min), ...]}`` sorted by start_min.
    """
    from app.models.operator import OperatorCalendar, Shift

    if not operator_ids:
        return {}

    rows = (
        await db.execute(
            select(
                OperatorCalendar.operator_id,
                OperatorCalendar.date,
                OperatorCalendar.is_available,
                OperatorCalendar.shift_id,
                Shift.start_time,
                Shift.end_time,
                Shift.break_duration_minutes,
            )
            .outerjoin(Shift, OperatorCalendar.shift_id == Shift.id)
            .where(
                and_(
                    OperatorCalendar.operator_id.in_(operator_ids),
                    OperatorCalendar.date >= start_date,
                    OperatorCalendar.date <= end_date,
                )
            )
            .order_by(OperatorCalendar.operator_id, OperatorCalendar.date)
        )
    ).all()

    result: dict[uuid.UUID, list[tuple[int, int]]] = defaultdict(list)

    for row in rows:
        if not row.is_available or row.shift_id is None or row.start_time is None:
            continue
        day_slots = _shift_slots_for_day(
            day=row.date,
            shift_start_time=row.start_time,
            shift_end_time=row.end_time,
            break_duration_minutes=row.break_duration_minutes,
            epoch=epoch,
        )
        result[row.operator_id].extend(day_slots)

    # Ensure all requested operators have an entry (possibly empty)
    for op_id in operator_ids:
        if op_id not in result:
            result[op_id] = []

    return dict(result)


# ─── Unavailable intervals (inverse of available slots) ───────────────────────

def build_unavailable_intervals(
    operator_id: uuid.UUID,
    all_slots: list[tuple[int, int]],
    horizon_minutes: int,
    epoch: datetime,
) -> list[tuple[int, int]]:
    """Return the complement of *all_slots* within [0, horizon_minutes].

    Used to construct fixed IntervalVar objects in CP-SAT that represent
    periods when an operator is unavailable.

    Example:
        all_slots = [(100, 200)], horizon = 300
        → unavailable = [(0, 100), (200, 300)]
    """
    _ = operator_id  # reserved for future per-operator filtering
    _ = epoch        # reserved for future use

    if not all_slots:
        return [(0, horizon_minutes)] if horizon_minutes > 0 else []

    # Sort by start time (should already be sorted but be defensive)
    sorted_slots = sorted(all_slots, key=lambda s: s[0])

    unavailable: list[tuple[int, int]] = []
    cursor = 0

    for start, end in sorted_slots:
        if cursor < start:
            unavailable.append((cursor, start))
        cursor = max(cursor, end)

    if cursor < horizon_minutes:
        unavailable.append((cursor, horizon_minutes))

    return unavailable
