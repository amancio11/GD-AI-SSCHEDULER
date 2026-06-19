"""Router: Operations — aggiornamento stato/avanzamento delle operazioni.

Questo router espone l'endpoint REALE che il frontend (OperationSimulator.tsx)
già chiama da tempo: `PATCH /api/operations/{operation_id}/status`. Prima di
questa implementazione l'endpoint non esisteva lato backend.

FLUSSO COMPLETO DI UN AGGIORNAMENTO STATO
==========================================
1. Il planner/operatore aggiorna lo stato di un'operazione (es. COMPLETED con
   actual_end successivo a scheduled_end → ritardo).
2. `transitions.transition_operation_status(...)` calcola l'esito puro:
   nuovo stato entry, ritardo in minuti, urgenza di reschedule (NONE/SOFT/HARD).
3. Operation + ScheduleEntry collegata vengono aggiornati nella stessa
   transazione DB.
4. L'evento viene scritto in `operation_status_audit` (mai perso, anche per
   transizioni "inusuali" che lo schema permissivo comunque accetta).
5. `DelayPropagationEngine.apply(...)`:
     - crea un DelayEvent automatico se il ritardo è SOFT o HARD
     - fa il rollup dello stato del ProductionOrder lungo la BOM
     - se urgenza HARD → lancia reschedule_incremental via Celery
6. La risposta HTTP ritorna subito (il reschedule è asincrono); il frontend
   riceverà l'esito via WebSocket `RESCHEDULE_COMPLETE`.

NOTA TECNICA: sessione sincrona
--------------------------------
Il resto del router usa AsyncSession (FastAPI + asyncpg), ma il
DelayPropagationEngine e l'OrderStatusRollup leggono/scrivono con una Session
sincrona (stesso pattern già usato da reschedule_engine.py per Celery — vedi
GUIDA_TECNICA sezione Celery). Qui usiamo una sessione sincrona dedicata
APERTA E CHIUSA all'interno della singola richiesta, per riusare 1:1 la stessa
logica del Celery task senza duplicarla in versione async.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker, Session as SyncSession

from app.db.session import get_db
from app.enums import OperationStatus, ScheduleEntryStatus
from app.models.routing import Operation, Routing
from app.models.schedule import ScheduleEntry, ScheduleScenario
from app.models.production import ProductionOrder
from app.core.state_engine.transitions import transition_operation_status
from app.core.state_engine.delay_propagation import (
    DelayPropagationEngine,
    get_delay_threshold_minutes,
)
from app.core.state_engine.models_audit import OperationStatusAudit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/operations", tags=["operations"])


# ── Sessione sincrona dedicata (stesso pattern di reschedule_engine.py) ──────
_SYNC_DB_URL = (
    os.environ.get("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    .replace("postgresql://", "postgresql+psycopg2://")
    or "postgresql+psycopg2://scheduler:scheduler@localhost:5432/scheduler"
)
_sync_engine = create_engine(_SYNC_DB_URL, pool_pre_ping=True)
_SyncSession = sessionmaker(bind=_sync_engine, autocommit=False, autoflush=False)


def _get_sync_session() -> SyncSession:
    return _SyncSession()


# ── Schemas ───────────────────────────────────────────────────────────────────

class OperationStatusUpdate(BaseModel):
    status: OperationStatus
    progress_pct: float | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    interruption_reason: str | None = None
    # entry_id opzionale: se non fornito, il router cerca l'ultima entry
    # SCHEDULED/IN_PROGRESS/DELAYED collegata all'operazione nello scenario attivo.
    entry_id: uuid.UUID | None = None
    triggered_by: str = "operator_ui"


class OperationStatusUpdateResponse(BaseModel):
    operation_id: uuid.UUID
    new_operation_status: OperationStatus
    new_entry_status: ScheduleEntryStatus | None
    delay_minutes: int
    reschedule_urgency: str
    reschedule_triggered: bool
    reschedule_task_id: str | None
    delay_event_id: uuid.UUID | None
    rollup_changes: list[dict]
    is_unusual: bool
    warnings: list[str]
    audit_message: str


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.patch("/{operation_id}/status", response_model=OperationStatusUpdateResponse)
async def update_operation_status(
    operation_id: uuid.UUID,
    payload: OperationStatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> OperationStatusUpdateResponse:
    """Aggiorna lo stato di un'operazione e applica l'intero state engine.

    Schema PERMISSIVO: qualunque transizione è accettata. Una transizione
    "inusuale" (es. riapertura di un'operazione COMPLETED) viene applicata
    comunque, ma tracciata con `is_unusual=True` nell'audit e nella risposta,
    cosicché il frontend possa mostrare un warning non bloccante.
    """
    # ── 1. Carica operazione + routing + production_order (async, per validare input) ──
    op_result = await db.execute(
        select(Operation).where(Operation.id == operation_id)
    )
    operation = op_result.scalar_one_or_none()
    if operation is None:
        raise HTTPException(status_code=404, detail="Operazione non trovata")

    routing_result = await db.execute(
        select(Routing).where(Routing.id == operation.routing_id)
    )
    routing = routing_result.scalar_one_or_none()
    if routing is None:
        raise HTTPException(status_code=500, detail="Routing collegato non trovato (dato inconsistente)")

    po_result = await db.execute(
        select(ProductionOrder).where(ProductionOrder.id == routing.production_order_id)
    )
    production_order = po_result.scalar_one_or_none()
    if production_order is None:
        raise HTTPException(status_code=500, detail="ProductionOrder collegato non trovato")

    # ── 2. Trova la ScheduleEntry da aggiornare ───────────────────────────────
    if payload.entry_id is not None:
        entry_result = await db.execute(
            select(ScheduleEntry).where(ScheduleEntry.id == payload.entry_id)
        )
        entry = entry_result.scalar_one_or_none()
        if entry is None:
            raise HTTPException(status_code=404, detail="ScheduleEntry non trovata")
    else:
        entry_result = await db.execute(
            select(ScheduleEntry)
            .where(
                ScheduleEntry.operation_id == operation_id,
                ScheduleEntry.status.notin_([ScheduleEntryStatus.STALE]),
            )
            .order_by(ScheduleEntry.scheduled_start.desc())
            .limit(1)
        )
        entry = entry_result.scalar_one_or_none()
        # Un'operazione può non avere ancora una entry (nessuno schedule
        # ancora calcolato): in quel caso aggiorniamo solo l'Operation.

    # ── 3. Calcola la transizione (logica pura, nessun I/O) ───────────────────
    transition = transition_operation_status(
        current_status=operation.status,
        new_status=payload.status,
        scheduled_end=entry.scheduled_end if entry else None,
        actual_end=payload.actual_end,
        delay_threshold_minutes=get_delay_threshold_minutes(),
        interruption_reason=payload.interruption_reason,
    )

    # ── 4. Persiste Operation + ScheduleEntry (sessione async) ────────────────
    operation.status = transition.operation_status
    if payload.progress_pct is not None:
        operation.progress_pct = payload.progress_pct
    if payload.actual_end is not None and transition.operation_status == OperationStatus.COMPLETED:
        operation.actual_duration_minutes = _safe_duration_minutes(
            entry.actual_start if entry and entry.actual_start else payload.actual_start,
            payload.actual_end,
        )

    if entry is not None:
        entry.status = transition.entry_status
        if payload.actual_start is not None:
            entry.actual_start = payload.actual_start
        if payload.actual_end is not None:
            entry.actual_end = payload.actual_end
        entry.delay_minutes = transition.delay_minutes
        if payload.interruption_reason is not None:
            entry.interruption_reason = payload.interruption_reason

    await db.commit()
    await db.refresh(operation)

    # ── 5. Audit trail (sempre, indipendentemente dall'urgenza) ──────────────
    await _write_audit_async(
        db,
        entity_type="operation",
        entity_id=operation.id,
        transition=transition,
        triggered_by=payload.triggered_by,
    )

    # ── 6. Propagazione effetti collaterali (sessione SINCRONA dedicata) ──────
    # Riusiamo una sessione sync per condividere 1:1 la logica già scritta
    # per il Celery reschedule_engine (OrderStatusRollup, trigger Celery).
    sync_session = _get_sync_session()
    try:
        engine = DelayPropagationEngine(sync_session)
        outcome = engine.apply(
            operation=sync_session.get(Operation, operation.id),
            transition=transition,
            machine_order_id=production_order.machine_order_id,
            production_order_id=production_order.id,
            triggered_by=f"operation_status_update:{operation.id}",
        )
        sync_session.commit()
    except Exception:
        sync_session.rollback()
        logger.exception(
            "Errore durante la propagazione degli effetti collaterali per operazione %s",
            operation_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Stato aggiornato ma la propagazione degli effetti (DelayEvent/rollup/reschedule) è fallita. Controllare i log.",
        ) from None
    finally:
        sync_session.close()

    return OperationStatusUpdateResponse(
        operation_id=operation.id,
        new_operation_status=transition.operation_status,
        new_entry_status=transition.entry_status if entry is not None else None,
        delay_minutes=transition.delay_minutes,
        reschedule_urgency=transition.reschedule_urgency.value,
        reschedule_triggered=outcome.reschedule_triggered,
        reschedule_task_id=outcome.reschedule_task_id,
        delay_event_id=outcome.delay_event_id,
        rollup_changes=[
            {
                "production_order_id": str(c.production_order_id),
                "material_code": c.material_code,
                "old_status": c.old_status.value,
                "new_status": c.new_status.value,
            }
            for c in outcome.rollup_changes
        ],
        is_unusual=transition.is_unusual,
        warnings=transition.warnings,
        audit_message=transition.audit_message,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_duration_minutes(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() // 60))


async def _write_audit_async(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    transition,
    triggered_by: str,
) -> None:
    audit = OperationStatusAudit(
        entity_type=entity_type,
        entity_id=entity_id,
        old_status=transition.previous_status.value,
        new_status=transition.operation_status.value,
        is_unusual=transition.is_unusual,
        delay_minutes=transition.delay_minutes,
        reschedule_urgency=transition.reschedule_urgency.value,
        audit_message=transition.audit_message,
        warnings_json=json.dumps(transition.warnings, ensure_ascii=False) if transition.warnings else None,
        triggered_by=triggered_by,
        created_at=datetime.now(tz=timezone.utc),
    )
    db.add(audit)
    await db.commit()


@router.get("/{operation_id}/audit-history")
async def get_operation_audit_history(
    operation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Storico completo delle transizioni di stato per un'operazione.

    Usato dal frontend (OperationSimulator) per mostrare un timeline delle
    modifiche, e dall'AI per `analyze-history`.
    """
    result = await db.execute(
        select(OperationStatusAudit)
        .where(
            OperationStatusAudit.entity_type == "operation",
            OperationStatusAudit.entity_id == operation_id,
        )
        .order_by(OperationStatusAudit.created_at.desc())
    )
    rows = list(result.scalars().all())
    return [
        {
            "id": str(r.id),
            "old_status": r.old_status,
            "new_status": r.new_status,
            "is_unusual": r.is_unusual,
            "delay_minutes": r.delay_minutes,
            "reschedule_urgency": r.reschedule_urgency,
            "audit_message": r.audit_message,
            "warnings": json.loads(r.warnings_json) if r.warnings_json else [],
            "triggered_by": r.triggered_by,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]