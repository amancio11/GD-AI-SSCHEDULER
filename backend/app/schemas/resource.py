"""Pydantic schemas per ResourceType (risorse a capacità di gruppo)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.enums import SkillType


class WeekdayAvailability(BaseModel):
    count: int = 0
    hours: float = 0.0


# weekday_schedule: { "0": {count, hours}, ... "6": {...} }  (0=lun … 6=dom)
WeekdaySchedule = dict[str, WeekdayAvailability]


class ResourceTypeBase(BaseModel):
    name: str | None = None
    workcenter_id: uuid.UUID
    skill: SkillType
    daily_capacity_hours: float = 8.0
    count: int = 1
    weekday_schedule: WeekdaySchedule | None = None
    is_active: bool = True


class ResourceTypeCreate(ResourceTypeBase):
    pass


class ResourceTypeUpdate(BaseModel):
    name: str | None = None
    workcenter_id: uuid.UUID | None = None
    skill: SkillType | None = None
    daily_capacity_hours: float | None = None
    count: int | None = None
    weekday_schedule: WeekdaySchedule | None = None
    is_active: bool | None = None


class ResourceTypeRead(ResourceTypeBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
