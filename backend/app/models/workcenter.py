"""Workcenter and SkillWorkcenterMapping SQLAlchemy models."""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import SkillType
from app.models.base import Base, UUIDMixin


class Workcenter(UUIDMixin, Base):
    __tablename__ = "workcenters"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    skill_mappings: Mapped[list[SkillWorkcenterMapping]] = relationship(
        "SkillWorkcenterMapping", back_populates="workcenter", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Workcenter code={self.code!r} location={self.location!r}>"


class SkillWorkcenterMapping(UUIDMixin, Base):
    __tablename__ = "skill_workcenter_mapping"
    __table_args__ = (
        UniqueConstraint("skill", "workcenter_id", name="uq_skill_workcenter"),
    )

    skill: Mapped[SkillType] = mapped_column(
        Enum(SkillType, name="skilltype"), nullable=False
    )
    workcenter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workcenters.id"), nullable=False
    )
    can_do_electrical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_do_mechanical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_do_general: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Relationships
    workcenter: Mapped[Workcenter] = relationship(
        "Workcenter", back_populates="skill_mappings", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<SkillWorkcenterMapping skill={self.skill!r} "
            f"workcenter_id={self.workcenter_id!r}>"
        )
