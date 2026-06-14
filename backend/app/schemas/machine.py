"""Pydantic v2 schemas for MachineModel and MachineOrder."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.enums import MachineOrderStatus


# ── MachineModel ──────────────────────────────────────────────────────────────

class MachineModelBase(BaseModel):
    code: str
    name: str
    description: str | None = None


class MachineModelCreate(MachineModelBase):
    pass


class MachineModelUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class MachineModelRead(MachineModelBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── MachineOrder ──────────────────────────────────────────────────────────────

class MachineOrderBase(BaseModel):
    sap_order_id: str
    machine_model_id: uuid.UUID
    description: str | None = None
    status: MachineOrderStatus = MachineOrderStatus.PLANNED
    workcenter_id: uuid.UUID | None = None


class MachineOrderCreate(MachineOrderBase):
    pass


class MachineOrderUpdate(BaseModel):
    description: str | None = None
    status: MachineOrderStatus | None = None
    workcenter_id: uuid.UUID | None = None


class MachineOrderRead(MachineOrderBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
