"""ScheduleScenario and ScheduleEntry SQLAlchemy models."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import ObjectiveMode, ScheduleEntryStatus
from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.machine import MachineOrder
    from app.models.operator import Operator
    from app.models.routing import Operation
    from app.models.workcenter import Workcenter


class ScheduleScenario(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "schedule_scenarios"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    machine_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("machine_orders.id"), nullable=False, index=True
    )
    objective_mode: Mapped[ObjectiveMode] = mapped_column(
        Enum(ObjectiveMode, name="objectivemode"),
        nullable=False,
        default=ObjectiveMode.FINISH_BY_DATE,
    )
    target_finish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    resource_set_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    machine_order: Mapped[MachineOrder] = relationship(
        "MachineOrder", back_populates="scenarios", lazy="selectin"
    )
    entries: Mapped[list[ScheduleEntry]] = relationship(
        "ScheduleEntry", back_populates="scenario", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<ScheduleScenario name={self.name!r} "
            f"objective={self.objective_mode!r} active={self.is_active}>"
        )


class ScheduleEntry(UUIDMixin, Base):
    __tablename__ = "schedule_entries"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedule_scenarios.id"), nullable=False, index=True
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False, index=True
    )
    operator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operators.id"), nullable=False
    )
    workcenter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workcenters.id"), nullable=False
    )
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[ScheduleEntryStatus] = mapped_column(
        Enum(ScheduleEntryStatus, name="scheduleentrystatus"),
        nullable=False,
        default=ScheduleEntryStatus.SCHEDULED,
    )
    interruption_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_manual_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Relationships
    scenario: Mapped[ScheduleScenario] = relationship(
        "ScheduleScenario", back_populates="entries", lazy="selectin"
    )
    operation: Mapped[Operation] = relationship("Operation", lazy="selectin")
    operator: Mapped[Operator] = relationship("Operator", lazy="selectin")
    workcenter: Mapped[Workcenter] = relationship("Workcenter", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<ScheduleEntry operation_id={self.operation_id!r} "
            f"start={self.scheduled_start!r} status={self.status!r}>"
        )
