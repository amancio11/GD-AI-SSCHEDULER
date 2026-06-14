"""Pydantic v2 schemas for Operator, Shift and OperatorCalendar."""
from __future__ import annotations

import uuid
from datetime import date, time

from pydantic import BaseModel, ConfigDict

from app.enums import SkillType


# ── Shift ─────────────────────────────────────────────────────────────────────

class ShiftBase(BaseModel):
    name: str
    start_time: time
    end_time: time
    break_duration_minutes: int = 30
    is_active: bool = True


class ShiftCreate(ShiftBase):
    pass


class ShiftRead(ShiftBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── Operator ──────────────────────────────────────────────────────────────────

class OperatorBase(BaseModel):
    employee_id: str
    full_name: str
    skill: SkillType
    workcenter_id: uuid.UUID
    is_active: bool = True


class OperatorCreate(OperatorBase):
    pass


class OperatorUpdate(BaseModel):
    full_name: str | None = None
    skill: SkillType | None = None
    workcenter_id: uuid.UUID | None = None
    is_active: bool | None = None


class OperatorRead(OperatorBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── OperatorCalendar ──────────────────────────────────────────────────────────

class OperatorCalendarBase(BaseModel):
    operator_id: uuid.UUID
    date: date
    shift_id: uuid.UUID | None = None
    is_available: bool = True
    notes: str | None = None
    override_reason: str | None = None


class OperatorCalendarCreate(OperatorCalendarBase):
    pass


class OperatorCalendarUpdate(BaseModel):
    shift_id: uuid.UUID | None = None
    is_available: bool | None = None
    notes: str | None = None
    override_reason: str | None = None


class OperatorCalendarRead(OperatorCalendarBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── Bulk calendar update ──────────────────────────────────────────────────────

class CalendarBulkUpdateRequest(BaseModel):
    operator_ids: list[uuid.UUID]
    date_from: date
    date_to: date
    shift_id: uuid.UUID | None = None
    is_available: bool = True
