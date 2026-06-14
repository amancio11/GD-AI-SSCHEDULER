"""Routing and Operation SQLAlchemy models."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean, Enum, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import ExecutionMode, OperationStatus, OperationType
from app.models.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.production import ProductionOrder
    from app.models.reference import ReferencePoint
    from app.models.workcenter import Workcenter


class Routing(UUIDMixin, Base):
    __tablename__ = "routings"

    production_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("production_orders.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    sap_routing_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_mode: Mapped[ExecutionMode] = mapped_column(
        Enum(ExecutionMode, name="executionmode"),
        nullable=False,
        default=ExecutionMode.SIMULTANEOUS,
    )

    # Relationships
    production_order: Mapped[ProductionOrder] = relationship(
        "ProductionOrder", back_populates="routing", lazy="selectin"
    )
    operations: Mapped[list[Operation]] = relationship(
        "Operation", back_populates="routing", lazy="selectin",
        order_by="Operation.sequence_number",
    )

    def __repr__(self) -> str:
        return f"<Routing production_order_id={self.production_order_id!r}>"


class Operation(UUIDMixin, Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("routing_id", "sequence_number", name="uq_routing_seq"),
    )

    routing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("routings.id"), nullable=False, index=True
    )
    sap_operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_type: Mapped[OperationType] = mapped_column(
        Enum(OperationType, name="operationtype"), nullable=False
    )
    workcenter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workcenters.id"), nullable=True
    )
    planned_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[OperationStatus] = mapped_column(
        Enum(OperationStatus, name="operationstatus"),
        nullable=False,
        default=OperationStatus.PENDING,
    )
    reference_point_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reference_points.id"), nullable=True
    )
    can_be_interrupted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    routing: Mapped[Routing] = relationship(
        "Routing", back_populates="operations", lazy="selectin"
    )
    workcenter: Mapped[Workcenter | None] = relationship("Workcenter", lazy="selectin")
    reference_point: Mapped[ReferencePoint | None] = relationship(
        "ReferencePoint", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<Operation routing_id={self.routing_id!r} "
            f"seq={self.sequence_number} type={self.operation_type!r} "
            f"status={self.status!r}>"
        )
