"""MachineModel and MachineOrder SQLAlchemy models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import MachineOrderStatus
from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.production import ProductionOrder
    from app.models.reference import ReferencePoint
    from app.models.schedule import ScheduleScenario
    from app.models.workcenter import Workcenter


class MachineModel(UUIDMixin, Base):
    __tablename__ = "machine_models"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    machine_orders: Mapped[list[MachineOrder]] = relationship(
        "MachineOrder", back_populates="machine_model", lazy="selectin"
    )
    reference_points: Mapped[list[ReferencePoint]] = relationship(
        "ReferencePoint", back_populates="machine_model", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<MachineModel code={self.code!r} name={self.name!r}>"


class MachineOrder(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "machine_orders"

    sap_order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    machine_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("machine_models.id"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[MachineOrderStatus] = mapped_column(
        Enum(MachineOrderStatus, name="machineorderstatus"),
        nullable=False,
        default=MachineOrderStatus.PLANNED,
    )
    workcenter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workcenters.id"), nullable=True
    )

    # Relationships
    machine_model: Mapped[MachineModel] = relationship(
        "MachineModel", back_populates="machine_orders", lazy="selectin"
    )
    workcenter: Mapped[Workcenter | None] = relationship(
        "Workcenter", lazy="selectin"
    )
    production_orders: Mapped[list[ProductionOrder]] = relationship(
        "ProductionOrder", back_populates="machine_order", lazy="selectin"
    )
    scenarios: Mapped[list[ScheduleScenario]] = relationship(
        "ScheduleScenario", back_populates="machine_order", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<MachineOrder sap_order_id={self.sap_order_id!r} status={self.status!r}>"
