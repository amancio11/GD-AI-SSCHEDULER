"""Pydantic v2 schemas for ProductionOrder, ZOrdersLink and BOMTreeNode."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.enums import ProductionOrderLevel, ProductionOrderStatus


# ── ProductionOrder ───────────────────────────────────────────────────────────

class ProductionOrderBase(BaseModel):
    sap_order_id: str
    parent_order_id: uuid.UUID | None = None
    parent_material: str | None = None
    machine_order_id: uuid.UUID
    level: ProductionOrderLevel
    material_code: str
    description: str | None = None
    quantity: int = 1
    unit: str = "PZ"
    workcenter_id: uuid.UUID | None = None
    progress_pct: float = 0.0
    status: ProductionOrderStatus = ProductionOrderStatus.PLANNED
    missing_arrival_date: datetime | None = None
    is_purchase_component: bool = False
    is_production_component_untracked: bool = False


class ProductionOrderCreate(ProductionOrderBase):
    pass


class ProductionOrderUpdate(BaseModel):
    description: str | None = None
    status: ProductionOrderStatus | None = None
    progress_pct: float | None = None
    missing_arrival_date: datetime | None = None


class ProductionOrderRead(ProductionOrderBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime


# ── BOMTreeNode (recursive) ───────────────────────────────────────────────────

class BOMTreeNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sap_order_id: str
    material_code: str
    description: str | None
    level: ProductionOrderLevel
    status: ProductionOrderStatus
    progress_pct: float
    workcenter_id: uuid.UUID | None
    is_purchase_component: bool
    is_production_component_untracked: bool
    missing_arrival_date: datetime | None
    children: list[BOMTreeNode] = []


# ── ZOrdersLink ───────────────────────────────────────────────────────────────

class ZOrdersLinkBase(BaseModel):
    child_order_id: uuid.UUID
    parent_order_id: uuid.UUID
    parent_material: str | None = None
    child_material: str | None = None
    level: str | None = None
    link_type: str | None = None


class ZOrdersLinkCreate(ZOrdersLinkBase):
    pass


class ZOrdersLinkRead(ZOrdersLinkBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
