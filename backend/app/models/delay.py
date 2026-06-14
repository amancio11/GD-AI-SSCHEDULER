"""DelayEvent SQLAlchemy model."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import DelayEventType
from app.models.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.machine import MachineOrder


class DelayEvent(UUIDMixin, Base):
    __tablename__ = "delay_events"

    machine_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("machine_orders.id"), nullable=False, index=True
    )
    event_type: Mapped[DelayEventType] = mapped_column(
        Enum(DelayEventType, name="delayeventtype"), nullable=False
    )
    affected_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    affected_entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delay_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delay_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requires_reschedule: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    machine_order: Mapped[MachineOrder] = relationship("MachineOrder", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<DelayEvent type={self.event_type!r} "
            f"from={self.delay_from!r} until={self.delay_until!r}>"
        )
