"""ReferencePoint and ReferencePointPrecedence SQLAlchemy models."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import TargetLevel
from app.models.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.machine import MachineModel


class ReferencePoint(UUIDMixin, Base):
    __tablename__ = "reference_points"
    __table_args__ = (
        UniqueConstraint("code", "machine_model_id", name="uq_rp_code_model"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    machine_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("machine_models.id"), nullable=False, index=True
    )
    target_level: Mapped[TargetLevel] = mapped_column(
        Enum(TargetLevel, name="targetlevel"), nullable=False
    )
    target_order_material: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Relationships
    machine_model: Mapped[MachineModel] = relationship(
        "MachineModel", back_populates="reference_points", lazy="selectin"
    )
    successors: Mapped[list[ReferencePointPrecedence]] = relationship(
        "ReferencePointPrecedence",
        foreign_keys="ReferencePointPrecedence.predecessor_reference_point_id",
        lazy="selectin",
    )
    predecessors: Mapped[list[ReferencePointPrecedence]] = relationship(
        "ReferencePointPrecedence",
        foreign_keys="ReferencePointPrecedence.reference_point_id",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<ReferencePoint code={self.code!r} level={self.target_level!r}>"


class ReferencePointPrecedence(UUIDMixin, Base):
    __tablename__ = "reference_point_precedences"
    __table_args__ = (
        UniqueConstraint(
            "reference_point_id",
            "predecessor_reference_point_id",
            name="uq_rpp_pair",
        ),
    )

    reference_point_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reference_points.id"), nullable=False, index=True
    )
    predecessor_reference_point_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reference_points.id"), nullable=False, index=True
    )
    machine_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("machine_models.id"), nullable=False
    )

    # Relationships
    reference_point: Mapped[ReferencePoint] = relationship(
        "ReferencePoint",
        foreign_keys=[reference_point_id],
        lazy="selectin",
        overlaps="predecessors",
    )
    predecessor: Mapped[ReferencePoint] = relationship(
        "ReferencePoint",
        foreign_keys=[predecessor_reference_point_id],
        lazy="selectin",
        overlaps="successors",
    )

    def __repr__(self) -> str:
        return (
            f"<ReferencePointPrecedence "
            f"rp={self.reference_point_id!r} pred={self.predecessor_reference_point_id!r}>"
        )
