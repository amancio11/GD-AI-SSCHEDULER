"""AiSuggestion and AiChatSession SQLAlchemy models."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import AiSuggestionType
from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.machine import MachineOrder
    from app.models.schedule import ScheduleScenario


class AiSuggestion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ai_suggestions"

    scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedule_scenarios.id"), nullable=True, index=True
    )
    machine_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("machine_orders.id"), nullable=False, index=True
    )
    suggestion_type: Mapped[AiSuggestionType] = mapped_column(
        Enum(AiSuggestionType, name="aisuggestiontype"), nullable=False
    )
    suggestion_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_actions_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Relationships
    scenario: Mapped[ScheduleScenario | None] = relationship(
        "ScheduleScenario", lazy="selectin"
    )
    machine_order: Mapped[MachineOrder] = relationship("MachineOrder", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<AiSuggestion type={self.suggestion_type!r} "
            f"confidence={self.confidence_score!r} accepted={self.accepted}>"
        )


class AiChatSession(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ai_chat_sessions"

    scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schedule_scenarios.id"), nullable=True, index=True
    )
    machine_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("machine_orders.id"), nullable=False, index=True
    )
    messages_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_activity: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    scenario: Mapped[ScheduleScenario | None] = relationship(
        "ScheduleScenario", lazy="selectin"
    )
    machine_order: Mapped[MachineOrder] = relationship("MachineOrder", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<AiChatSession machine_order_id={self.machine_order_id!r} "
            f"last_activity={self.last_activity!r}>"
        )
