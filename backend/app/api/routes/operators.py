"""Router: Operatori, turni e calendario delle disponibilità.

Questo modulo gestisce le risorse umane del sistema di scheduling:
  - Operators: gli assemblatori con skill fissa (ELECTRICAL, MECHANICAL, MULTI)
    e workcenter fisso (non si spostano tra sedi)
  - Shifts: i turni standard di lavoro (Mattina, Pomeriggio, Notte)
  - OperatorCalendar: il calendario giornaliero delle disponibilità;
    il CP-SAT preprocessa questo calendario prima di costruire il modello

Concetto chiave di dominio:
  Un operatore ELECTRICAL può lavorare SOLO su operazioni di tipo ELECTRICAL
  nel SUO workcenter. Un MULTI può fare tutto. Questo vincolo è già codificato
  nelle tabelle skill_workcenter_mapping e applicato dallo shift_preprocessor.
"""
from __future__ import annotations

import uuid
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.operator import Operator, Shift, OperatorCalendar
from app.schemas.operator import (
    OperatorRead,
    OperatorCreate,
    OperatorUpdate,
    ShiftRead,
    OperatorCalendarRead,
    OperatorCalendarCreate,
    OperatorCalendarUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/operators", tags=["operators"])


# ─────────────────────────────────────────────────────────────────────────────
# Operators CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[OperatorRead])
async def list_operators(
    page: int = 1,
    size: int = 100,
    workcenter_id: uuid.UUID | None = Query(default=None),
    skill: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[Operator]:
    """Elenca gli operatori con filtri facoltativi per workcenter e skill.

    La lista viene usata dal frontend per:
    - Popolare il calendario degli operatori (pagina OperatorCalendar)
    - Filtrare il Gantt per reparto/skill
    - Alimentare il solver CP-SAT con l'insieme di risorse disponibili
    """
    query = select(Operator).where(Operator.is_active == True)  # noqa: E712
    if workcenter_id:
        query = query.where(Operator.workcenter_id == workcenter_id)
    if skill:
        query = query.where(Operator.skill == skill)
    query = query.order_by(Operator.full_name).offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{operator_id}", response_model=OperatorRead)
async def get_operator(
    operator_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Operator:
    """Recupera un operatore per ID."""
    obj = await db.get(Operator, operator_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Operatore non trovato")
    return obj


@router.post("", response_model=OperatorRead, status_code=201)
async def create_operator(
    payload: OperatorCreate,
    db: AsyncSession = Depends(get_db),
) -> Operator:
    """Crea un nuovo operatore nel sistema.

    Ogni operatore è associato a un workcenter fisso (sede): non può essere
    spostato a runtime dallo scheduler. Questa scelta semplifica il modello
    CP-SAT evitando vincoli di trasferta.
    """
    op = Operator(**payload.model_dump())
    db.add(op)
    await db.commit()
    await db.refresh(op)
    return op


@router.patch("/{operator_id}", response_model=OperatorRead)
async def update_operator(
    operator_id: uuid.UUID,
    payload: OperatorUpdate,
    db: AsyncSession = Depends(get_db),
) -> Operator:
    """Aggiorna i dati di un operatore (skill, workcenter, stato attivo)."""
    obj = await db.get(Operator, operator_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Operatore non trovato")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# Shifts
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/shifts/all", response_model=list[ShiftRead])
async def list_shifts(db: AsyncSession = Depends(get_db)) -> list[Shift]:
    """Elenca i turni di lavoro disponibili (Mattina, Pomeriggio, Notte).

    I turni sono usati dallo shift_preprocessor.py per convertire il calendario
    operatori in slot interi (minuti dall'epoch) prima che il CP-SAT li legga.
    """
    result = await db.execute(select(Shift).where(Shift.is_active == True).order_by(Shift.name))  # noqa: E712
    return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────────────────
# Operator Calendar — disponibilità giornaliera
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{operator_id}/calendar", response_model=list[OperatorCalendarRead])
async def get_operator_calendar(
    operator_id: uuid.UUID,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[OperatorCalendar]:
    """Restituisce il calendario di un operatore in un intervallo di date.

    Ogni riga indica: in questa data, l'operatore è disponibile con quale turno?
    Se is_available=False, l'operatore è assente (malattia, ferie, ecc.) e il CP-SAT
    non gli assegnerà operazioni in quella giornata.
    """
    filters = [OperatorCalendar.operator_id == operator_id]
    if date_from:
        filters.append(OperatorCalendar.date >= date_from)
    if date_to:
        filters.append(OperatorCalendar.date <= date_to)

    result = await db.execute(
        select(OperatorCalendar)
        .where(and_(*filters))
        .order_by(OperatorCalendar.date)
    )
    return list(result.scalars().all())


@router.put("/{operator_id}/calendar/{cal_date}", response_model=OperatorCalendarRead)
async def upsert_operator_calendar(
    operator_id: uuid.UUID,
    cal_date: date,
    payload: OperatorCalendarCreate,
    db: AsyncSession = Depends(get_db),
) -> OperatorCalendar:
    """Inserisce o aggiorna la disponibilità di un operatore per una specifica data.

    Usa il pattern UPSERT (UPDATE OR INSERT): se esiste già una riga per
    (operator_id, date), la aggiorna; altrimenti la crea. Questo semplifica
    il frontend che non deve distinguere tra creazione e modifica.

    Un override_reason documenta il motivo di una modifica manuale rispetto
    al turno standard (es. "copertura straordinaria", "assenza pianificata").
    """
    # Cerca riga esistente
    result = await db.execute(
        select(OperatorCalendar).where(
            OperatorCalendar.operator_id == operator_id,
            OperatorCalendar.date == cal_date,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Aggiorna solo i campi presenti nel payload
        update_data = payload.model_dump(exclude={"operator_id", "date"}, exclude_unset=True)
        for field, value in update_data.items():
            setattr(existing, field, value)
        await db.commit()
        await db.refresh(existing)
        return existing
    else:
        # Crea nuova riga
        entry = OperatorCalendar(
            operator_id=operator_id,
            date=cal_date,
            shift_id=payload.shift_id,
            is_available=payload.is_available,
            notes=payload.notes,
            override_reason=payload.override_reason,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry


@router.post("/{operator_id}/calendar/bulk", response_model=list[OperatorCalendarRead])
async def bulk_upsert_operator_calendar(
    operator_id: uuid.UUID,
    entries: list[OperatorCalendarCreate],
    db: AsyncSession = Depends(get_db),
) -> list[OperatorCalendar]:
    """Inserisce o aggiorna il calendario di più date in una sola chiamata.

    Usato dalla pagina OperatorCalendar per salvare la configurazione di
    un'intera settimana/mese con un solo round-trip verso il backend.
    """
    result_list: list[OperatorCalendar] = []
    for payload in entries:
        existing = await db.execute(
            select(OperatorCalendar).where(
                OperatorCalendar.operator_id == operator_id,
                OperatorCalendar.date == payload.date,
            )
        )
        obj = existing.scalar_one_or_none()
        if obj:
            for field, value in payload.model_dump(exclude={"operator_id"}, exclude_unset=True).items():
                setattr(obj, field, value)
        else:
            obj = OperatorCalendar(operator_id=operator_id, **payload.model_dump(exclude={"operator_id"}))
            db.add(obj)
        result_list.append(obj)

    await db.commit()
    for obj in result_list:
        await db.refresh(obj)
    return result_list
