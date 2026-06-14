"""Router: Delay Event — eventi di ritardo che impattano lo schedule.

I delay event sono eventi che causano uno scorrimento temporale del piano:
  - OPERATOR_ABSENCE: un operatore è assente in modo imprevisto
  - COMPONENT_DELAY: un componente arriverà più tardi del previsto
  - MANUAL_OPERATION_DELAY: il planner registra manualmente un ritardo
  - OTHER: eventi generici (guasto macchinario, problema qualità, ecc.)

Flusso dopo la creazione di un delay event:
  1. Il frontend registra l'evento → POST /api/delays
  2. Se requires_reschedule=True, viene automaticamente triggerata
     una rischedulazione incrementale tramite Celery (solo le operazioni
     non ancora completate vengono rimosse e ripianificate)
  3. Il modulo AI analizza l'impatto del ritardo e propone azioni correttive
  4. Il planner riceve la notifica WebSocket con il nuovo piano
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.delay import DelayEvent
from app.schemas.delay import (
    DelayEventCreate,
    DelayEventUpdate,
    DelayEventRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/delays", tags=["delays"])


@router.get("/machine/{machine_order_id}", response_model=list[DelayEventRead])
async def list_delays(
    machine_order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[DelayEvent]:
    """Elenca tutti i delay event per una macchina, dal più recente.

    La Dashboard li usa per:
    - Mostrare gli alert urgenti nella sezione "Alert"
    - Alimentare la timeline degli eventi imminenti
    - Calcolare il numero di eventi che richiedono rischedulazione
    """
    result = await db.execute(
        select(DelayEvent)
        .where(DelayEvent.machine_order_id == machine_order_id)
        .order_by(DelayEvent.reported_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{delay_id}", response_model=DelayEventRead)
async def get_delay(
    delay_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DelayEvent:
    """Recupera un singolo delay event per ID."""
    obj = await db.get(DelayEvent, delay_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Delay event non trovato")
    return obj


@router.post("", response_model=DelayEventRead, status_code=201)
async def create_delay(
    payload: DelayEventCreate,
    db: AsyncSession = Depends(get_db),
) -> DelayEvent:
    """Registra un nuovo evento di ritardo.

    Se requires_reschedule=True, dopo la creazione viene automaticamente
    inviato il task Celery per rischedulare le operazioni ancora pianificate,
    rispettando il nuovo vincolo temporale introdotto dal ritardo.

    Il campo reported_at è impostato al timestamp corrente se non fornito.
    """
    # Usa il timestamp corrente come reported_at di default
    if not payload.reported_at:
        # reported_at arriva già dal client, ma gestiamo il caso in cui manchi
        data = payload.model_dump()
        data["reported_at"] = datetime.now(tz=timezone.utc)
    else:
        data = payload.model_dump()

    delay = DelayEvent(**data)
    db.add(delay)
    await db.commit()
    await db.refresh(delay)

    logger.info(
        "Delay event creato id=%s tipo=%s macchina=%s richiede_rischedulazione=%s",
        delay.id, delay.event_type, delay.machine_order_id, delay.requires_reschedule,
    )

    # Se il ritardo richiede rischedulazione, cerca lo scenario attivo e triggera il solver
    if delay.requires_reschedule:
        from app.models.schedule import ScheduleScenario
        from sqlalchemy import select as sql_select
        scenario_result = await db.execute(
            sql_select(ScheduleScenario).where(
                ScheduleScenario.machine_order_id == delay.machine_order_id,
                ScheduleScenario.is_active == True,  # noqa: E712
            )
        )
        active_scenario = scenario_result.scalar_one_or_none()
        if active_scenario:
            from app.core.scheduler.scheduler_orchestrator import run_schedule
            run_schedule(active_scenario.id, triggered_by=f"delay_event:{delay.id}")
            logger.info("Rischedulazione avviata per scenario=%s", active_scenario.id)

    return delay


@router.patch("/{delay_id}", response_model=DelayEventRead)
async def update_delay(
    delay_id: uuid.UUID,
    payload: DelayEventUpdate,
    db: AsyncSession = Depends(get_db),
) -> DelayEvent:
    """Aggiorna un delay event (es. proroga la data di fine, modifica la descrizione)."""
    obj = await db.get(DelayEvent, delay_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Delay event non trovato")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{delay_id}")
async def delete_delay(
    delay_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Rimuove un delay event (es. era un falso allarme)."""
    obj = await db.get(DelayEvent, delay_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Delay event non trovato")
    await db.delete(obj)
    await db.commit()
    return Response(status_code=204)
