"""OperationStatusAudit SQLAlchemy model.

Tabella di sola-append (mai UPDATE/DELETE applicativo) che registra OGNI
transizione di stato gestita dallo state engine — sia per le `Operation` sia
per i rollup di `ProductionOrder`. Serve a tre scopi:

  1. Audit / compliance: chi ha cambiato cosa e quando (schema permissivo:
     non blocchiamo transizioni "strane", ma le tracciamo sempre).
  2. Debug dei reschedule: capire perché un certo solve è partito.
  3. Materiale per l'AI (`analyze-history` in ai.py) per riconoscere pattern
     ricorrenti di ritardo (es. "il WC-BERGAMO ritarda sempre il lunedì").

Non sostituisce delay_events (che è un evento di business: "componente in
ritardo", "assenza operatore"): questa tabella è un log tecnico più granulare,
1 riga per ogni cambio di stato, incluse le transizioni senza ritardo.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin

if TYPE_CHECKING:
    pass


class OperationStatusAudit(UUIDMixin, Base):
    __tablename__ = "operation_status_audit"

    # Entità tracciata: tipicamente "operation", a volte "production_order"
    # quando il rollup BOM cambia lo stato di un ordine senza un'operazione
    # diretta a monte (es. propagazione verso un MACROAGGREGATE).
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    old_status: Mapped[str] = mapped_column(String(32), nullable=False)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)

    is_unusual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reschedule_urgency: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE")

    audit_message: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded list[str]

    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<OperationStatusAudit {self.entity_type}={self.entity_id!r} "
            f"{self.old_status} → {self.new_status!r}>"
        )