"""ProductionOrder and ZOrdersLink SQLAlchemy models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey,
    Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import ProductionOrderLevel, ProductionOrderStatus
from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.machine import MachineOrder
    from app.models.missing import MissingComponent
    from app.models.routing import Routing
    from app.models.workcenter import Workcenter


class ProductionOrder(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "production_orders"

    sap_order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    parent_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_orders.id"), nullable=True
    )
    parent_material: Mapped[str | None] = mapped_column(String(128), nullable=True)
    machine_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("machine_orders.id"), nullable=False, index=True
    )
    level: Mapped[ProductionOrderLevel] = mapped_column(
        Enum(ProductionOrderLevel, name="productionorderlevel"), nullable=False
    )
    material_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="PZ")
    workcenter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workcenters.id"), nullable=True
    )
    progress_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[ProductionOrderStatus] = mapped_column(
        Enum(ProductionOrderStatus, name="productionorderstatus"),
        nullable=False,
        default=ProductionOrderStatus.PLANNED,
    )
    missing_arrival_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_purchase_component: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_production_component_untracked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Relationships
    machine_order: Mapped[MachineOrder] = relationship(
        "MachineOrder", back_populates="production_orders", lazy="selectin"
    )
    workcenter: Mapped[Workcenter | None] = relationship("Workcenter", lazy="selectin")
    parent_order: Mapped[ProductionOrder | None] = relationship(
        "ProductionOrder", remote_side="ProductionOrder.id", lazy="selectin"
    )
    children: Mapped[list[ProductionOrder]] = relationship(
        "ProductionOrder",
        foreign_keys="ProductionOrder.parent_order_id",
        back_populates="parent_order",
        lazy="selectin",
    )
    routing: Mapped[Routing | None] = relationship(
        "Routing", back_populates="production_order", uselist=False, lazy="selectin"
    )
    missing_components: Mapped[list[MissingComponent]] = relationship(
        "MissingComponent", back_populates="production_order", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<ProductionOrder sap_order_id={self.sap_order_id!r} "
            f"level={self.level!r} status={self.status!r}>"
        )


class ZOrdersLink(UUIDMixin, Base):
    __tablename__ = "z_orders_link"

    child_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_orders.id"), nullable=False, index=True
    )
    parent_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_orders.id"), nullable=False, index=True
    )
    parent_material: Mapped[str | None] = mapped_column(String(128), nullable=True)
    child_material: Mapped[str | None] = mapped_column(String(128), nullable=True)
    level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    link_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Relationships
    child_order: Mapped[ProductionOrder] = relationship(
        "ProductionOrder",
        foreign_keys=[child_order_id],
        lazy="selectin",
    )
    parent_order: Mapped[ProductionOrder] = relationship(
        "ProductionOrder",
        foreign_keys=[parent_order_id],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<ZOrdersLink child={self.child_order_id!r} parent={self.parent_order_id!r}>"
        )
