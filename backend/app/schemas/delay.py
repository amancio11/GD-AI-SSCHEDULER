"""Pydantic v2 schemas for DelayEvent and DelayImpactResponse."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.enums import DelayEventType
from app.schemas.schedule import ScheduleEntryRead


# ── DelayEvent ────────────────────────────────────────────────────────────────

class DelayEventBase(BaseModel):
    machine_order_id: uuid.UUID
    event_type: DelayEventType
    affected_entity_id: uuid.UUID | None = None
    affected_entity_type: str | None = None
    delay_from: datetime
    delay_until: datetime
    description: str | None = None
    reported_at: datetime
    requires_reschedule: bool = True


class DelayEventCreate(DelayEventBase):
    pass


class DelayEventUpdate(BaseModel):
    delay_until: datetime | None = None
    description: str | None = None
    requires_reschedule: bool | None = None


class DelayEventRead(DelayEventBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── Delay impact ──────────────────────────────────────────────────────────────

class DelayImpactResponse(BaseModel):
    impacted_entries: list[ScheduleEntryRead]
    estimated_delta_days: float
    critical_path_affected: bool
