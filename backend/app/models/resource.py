"""ResourceType — capacità configurabile per gruppo (workcenter + skill).

Sostituisce il concetto di "operatore con nome" nello scheduling: una risorsa è
definita solo da workcenter, certificazione (skill) e capacità giornaliera in ore.
Più risorse dello stesso gruppo si sommano (es. due risorse da 8h → 16h/giorno).
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import SkillType
from app.models.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.models.workcenter import Workcenter


class ResourceType(UUIDMixin, Base):
    __tablename__ = "resource_types"
    __table_args__ = (
        UniqueConstraint("workcenter_id", "skill", name="uq_resource_type_wc_skill"),
    )

    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workcenter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workcenters.id"), nullable=False, index=True
    )
    skill: Mapped[SkillType] = mapped_column(
        Enum(SkillType, name="skilltype"), nullable=False
    )
    # Capacità giornaliera di UNA singola risorsa di questo tipo (ore).
    daily_capacity_hours: Mapped[float] = mapped_column(Float, nullable=False, default=8.0)
    # Quante risorse di questo tipo esistono. Capacità di gruppo = count × daily_capacity_hours.
    # (count e daily_capacity_hours fanno da DEFAULT lun–ven quando weekday_schedule è null.)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Disponibilità per giorno della settimana (override di count/ore).
    # Formato: {"0": {"count": 2, "hours": 8}, ... "6": {"count": 0, "hours": 0}}  (0=lun … 6=dom)
    # Se null → default: lun–ven = (count, daily_capacity_hours), sab/dom = 0.
    weekday_schedule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    workcenter: Mapped[Workcenter] = relationship("Workcenter", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<ResourceType wc={self.workcenter_id!r} skill={self.skill!r} "
            f"{self.count}×{self.daily_capacity_hours}h>"
        )
