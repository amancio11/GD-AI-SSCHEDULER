"""Pydantic v2 schemas for ReferencePoint and ReferencePointPrecedence."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.enums import TargetLevel


# ── ReferencePoint ────────────────────────────────────────────────────────────

class ReferencePointBase(BaseModel):
    code: str
    name: str
    machine_model_id: uuid.UUID
    target_level: TargetLevel
    target_order_material: str | None = None


class ReferencePointCreate(ReferencePointBase):
    pass


class ReferencePointUpdate(BaseModel):
    name: str | None = None
    target_level: TargetLevel | None = None
    target_order_material: str | None = None


class ReferencePointRead(ReferencePointBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── ReferencePointPrecedence ──────────────────────────────────────────────────

class ReferencePointPrecedenceBase(BaseModel):
    reference_point_id: uuid.UUID
    predecessor_reference_point_id: uuid.UUID
    machine_model_id: uuid.UUID


class ReferencePointPrecedenceCreate(ReferencePointPrecedenceBase):
    pass


class ReferencePointPrecedenceRead(ReferencePointPrecedenceBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── Bulk precedence update ────────────────────────────────────────────────────

class RPPrecedenceItem(BaseModel):
    rp_id: uuid.UUID
    predecessor_ids: list[uuid.UUID]


class RPPrecedenceUpdateRequest(BaseModel):
    machine_model_id: uuid.UUID
    precedences: list[RPPrecedenceItem]
