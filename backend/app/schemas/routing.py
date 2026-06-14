"""Pydantic v2 schemas for Routing and Operation."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.enums import ExecutionMode, OperationStatus, OperationType


# ── Routing ───────────────────────────────────────────────────────────────────

class RoutingBase(BaseModel):
    production_order_id: uuid.UUID
    sap_routing_id: str | None = None
    execution_mode: ExecutionMode = ExecutionMode.SIMULTANEOUS


class RoutingCreate(RoutingBase):
    pass


class RoutingUpdate(BaseModel):
    sap_routing_id: str | None = None
    execution_mode: ExecutionMode | None = None


class RoutingRead(RoutingBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── Operation ─────────────────────────────────────────────────────────────────

class OperationBase(BaseModel):
    routing_id: uuid.UUID
    sap_operation_id: str | None = None
    sequence_number: int
    description: str | None = None
    operation_type: OperationType
    workcenter_id: uuid.UUID | None = None
    planned_duration_minutes: int
    actual_duration_minutes: int | None = None
    progress_pct: float = 0.0
    status: OperationStatus = OperationStatus.PENDING
    reference_point_id: uuid.UUID | None = None
    can_be_interrupted: bool = True


class OperationCreate(OperationBase):
    pass


class OperationUpdate(BaseModel):
    status: OperationStatus | None = None
    progress_pct: float | None = None
    actual_duration_minutes: int | None = None
    reference_point_id: uuid.UUID | None = None


class OperationRead(OperationBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
