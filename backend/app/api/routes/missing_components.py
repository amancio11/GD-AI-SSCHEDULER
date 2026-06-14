"""Router: Componenti mancanti — materiali non ancora disponibili per il montaggio.

I componenti mancanti bloccano parzialmente o totalmente l'avanzamento
dei gruppi che li richiedono. Il sistema traccia:
  - Quali materiali mancano e per quale ordine
  - La data attesa di arrivo (stimata da SAP o inserita manualmente)
  - Se il componente è arrivato e quando è stato confermato

Il solver CP-SAT usa queste informazioni come vincolo:
  "Le operazioni del gruppo G non possono iniziare prima di arrival_date
   se G ha almeno un componente mancante non ancora arrivato."

Questo garantisce che lo schedule non pianifichi operazioni su un gruppo
per cui manca materiale — e che quando il materiale arriva, la rischedulazione
automatica rilasci il vincolo e assegni le operazioni.
"""
from __future__ import annotations

import uuid
import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.missing import MissingComponent
from app.schemas.missing import (
    MissingComponentCreate,
    MissingComponentUpdate,
    MissingComponentRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/missing-components", tags=["missing-components"])


@router.get("/machine/{machine_order_id}", response_model=list[MissingComponentRead])
async def list_missing_by_machine(
    machine_order_id: uuid.UUID,
    only_active: bool = True,
    db: AsyncSession = Depends(get_db),
) -> list[MissingComponent]:
    """Elenca i componenti mancanti per tutti gli ordini di una macchina.

    Con only_active=True (default) restituisce solo i materiali non ancora arrivati.
    Con only_active=False include anche quelli già arrivati (storico completo).

    Processo interno:
    1. Carica tutti i ProductionOrder della macchina.
    2. Per ogni ordine recupera i MissingComponent associati.
    Il risultato è una lista piatta, non gerarchica, per semplicità di visualizzazione.
    """
    # Join tra MissingComponent e ProductionOrder per filtrare per macchina
    from app.models.production import ProductionOrder
    query = (
        select(MissingComponent)
        .join(ProductionOrder, MissingComponent.production_order_id == ProductionOrder.id)
        .where(ProductionOrder.machine_order_id == machine_order_id)
        .order_by(MissingComponent.expected_arrival_date.asc().nullslast())
    )
    if only_active:
        query = query.where(MissingComponent.is_arrived == False)  # noqa: E712

    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{component_id}", response_model=MissingComponentRead)
async def get_missing_component(
    component_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> MissingComponent:
    """Recupera un singolo componente mancante per ID."""
    obj = await db.get(MissingComponent, component_id)
    if not obj:
        raise HTTPException(status_code=404, detail="MissingComponent non trovato")
    return obj


@router.post("", response_model=MissingComponentRead, status_code=201)
async def create_missing_component(
    payload: MissingComponentCreate,
    db: AsyncSession = Depends(get_db),
) -> MissingComponent:
    """Registra un componente mancante per un ordine di produzione.

    Tipicamente creato automaticamente dal seed o dalla sincronizzazione SAP,
    ma può essere creato manualmente dal planner quando scopre una mancanza
    non ancora registrata nel sistema ERP.
    """
    mc = MissingComponent(**payload.model_dump())
    db.add(mc)
    await db.commit()
    await db.refresh(mc)
    logger.info(
        "MissingComponent creato: %s ordine=%s arrivo=%s",
        mc.component_material, mc.production_order_id, mc.expected_arrival_date,
    )
    return mc


@router.patch("/{component_id}", response_model=MissingComponentRead)
async def update_missing_component(
    component_id: uuid.UUID,
    payload: MissingComponentUpdate,
    db: AsyncSession = Depends(get_db),
) -> MissingComponent:
    """Aggiorna un componente mancante.

    Caso d'uso principale: il magazzino conferma l'arrivo del materiale.
    Il planner imposta is_arrived=True e arrival_confirmed_date=oggi.

    Dopo la conferma arrivo, la prossima rischedulazione incrementale rimuoverà
    il vincolo di "attesa materiale" per le operazioni del gruppo interessato,
    permettendo al CP-SAT di pianificarle prima.
    """
    obj = await db.get(MissingComponent, component_id)
    if not obj:
        raise HTTPException(status_code=404, detail="MissingComponent non trovato")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)

    # Se viene confermato l'arrivo senza data esplicita, imposta la data odierna
    if payload.is_arrived and not obj.arrival_confirmed_date:
        obj.arrival_confirmed_date = date.today()

    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{component_id}")
async def delete_missing_component(
    component_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Rimuove un componente mancante dal tracking (es. era un errore di registrazione)."""
    obj = await db.get(MissingComponent, component_id)
    if not obj:
        raise HTTPException(status_code=404, detail="MissingComponent non trovato")
    await db.delete(obj)
    await db.commit()
    return Response(status_code=204)


@router.post("/{component_id}/confirm-arrival", response_model=MissingComponentRead)
async def confirm_arrival(
    component_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> MissingComponent:
    """Endpoint shortcut per confermare l'arrivo di un materiale.

    Equivalente a PATCH con is_arrived=True e arrival_confirmed_date=oggi.
    Fornito per semplicità di utilizzo dal frontend (singolo click "Arrivato!").
    """
    obj = await db.get(MissingComponent, component_id)
    if not obj:
        raise HTTPException(status_code=404, detail="MissingComponent non trovato")

    obj.is_arrived = True
    obj.arrival_confirmed_date = date.today()

    await db.commit()
    await db.refresh(obj)
    logger.info("Arrivo confermato: %s in data %s", obj.component_material, obj.arrival_confirmed_date)
    return obj
