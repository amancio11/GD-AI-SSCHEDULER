"""Pydantic v2 schemas for Workcenter and SkillWorkcenterMapping."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.enums import SkillType


# ── Workcenter ────────────────────────────────────────────────────────────────

class WorkcenterBase(BaseModel):
    code: str
    name: str
    location: str | None = None
    description: str | None = None
    is_active: bool = True


class WorkcenterCreate(WorkcenterBase):
    pass


class WorkcenterUpdate(BaseModel):
    name: str | None = None
    location: str | None = None
    description: str | None = None
    is_active: bool | None = None


class WorkcenterRead(WorkcenterBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── SkillWorkcenterMapping ────────────────────────────────────────────────────

class SkillWorkcenterMappingBase(BaseModel):
    skill: SkillType
    workcenter_id: uuid.UUID
    can_do_electrical: bool = False
    can_do_mechanical: bool = False
    can_do_general: bool = False


class SkillWorkcenterMappingCreate(SkillWorkcenterMappingBase):
    pass


class SkillWorkcenterMappingRead(SkillWorkcenterMappingBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
