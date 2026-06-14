"""Operator, Shift and OperatorCalendar SQLAlchemy models."""
from __future__ import annotations

import uuid
from datetime import date, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean, Date, Enum, ForeignKey, String, Text, Time, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import SkillType
from app.models.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.workcenter import Workcenter


class Operator(UUIDMixin, Base):
    __tablename__ = "operators"

    employee_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    skill: Mapped[SkillType] = mapped_column(
        Enum(SkillType, name="skilltype"), nullable=False
    )
    workcenter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workcenters.id"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    workcenter: Mapped[Workcenter] = relationship("Workcenter", lazy="selectin")
    calendar: Mapped[list[OperatorCalendar]] = relationship(
        "OperatorCalendar", back_populates="operator", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<Operator employee_id={self.employee_id!r} "
            f"name={self.full_name!r} skill={self.skill!r}>"
        )


class Shift(UUIDMixin, Base):
    __tablename__ = "shifts"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    break_duration_minutes: Mapped[int] = mapped_column(nullable=False, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return (
            f"<Shift name={self.name!r} "
            f"start={self.start_time!r} end={self.end_time!r}>"
        )


class OperatorCalendar(UUIDMixin, Base):
    __tablename__ = "operator_calendar"
    __table_args__ = (
        UniqueConstraint("operator_id", "date", name="uq_operator_calendar_date"),
    )

    operator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operators.id"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    shift_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=True
    )
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Relationships
    operator: Mapped[Operator] = relationship(
        "Operator", back_populates="calendar", lazy="selectin"
    )
    shift: Mapped[Shift | None] = relationship("Shift", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<OperatorCalendar operator_id={self.operator_id!r} "
            f"date={self.date!r} available={self.is_available}>"
        )
