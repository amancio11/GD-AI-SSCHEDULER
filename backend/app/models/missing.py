"""MissingComponent SQLAlchemy model."""
from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.production import ProductionOrder


class MissingComponent(UUIDMixin, Base):
    __tablename__ = "missing_components"
    __table_args__ = (
        UniqueConstraint(
            "production_order_id", "component_material", name="uq_missing_po_material"
        ),
    )

    production_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("production_orders.id"), nullable=False, index=True
    )
    component_material: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_arrival_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_arrived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    arrival_confirmed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    manually_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    production_order: Mapped[ProductionOrder] = relationship(
        "ProductionOrder", back_populates="missing_components", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<MissingComponent material={self.component_material!r} "
            f"arrived={self.is_arrived} arrival={self.expected_arrival_date!r}>"
        )
