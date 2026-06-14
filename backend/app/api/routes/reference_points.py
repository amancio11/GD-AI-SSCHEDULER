"""Router: Reference Point e precedenze del DAG.

I Reference Point (RP) sono il meccanismo di vincolo che determina l'ORDINE
di montaggio dei sottoassemblaggi di una macchina.

Concetto di dominio:
  Ogni RP è associato a un macroaggregato o aggregato specifico del modello macchina.
  Le operazioni dell'ordine macchina hanno un RP associato: questa operazione NON può
  iniziare finché il sottoassemblaggio identificato da RP non è completato.

  Esempio concreto per TURBOPRESS-X500:
  - RP-001 → "Struttura Portante" (nessun predecessore → inizia subito)
  - RP-002 → "Gruppo Idraulico" (predecessore: RP-001 → prima devi avere la struttura)
  - RP-003 → "Quadro Elettrico" (predecessore: RP-001 → può partire in parallelo con RP-002)

Le precedenze formano un DAG (grafo aciclico diretto). Il dag_builder.py
usa networkx per validare l'aciclicità e calcolare l'ordine topologico
prima di costruire i vincoli CP-SAT.
"""
from __future__ import annotations

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.reference import ReferencePoint, ReferencePointPrecedence
from app.schemas.reference import (
    ReferencePointCreate,
    ReferencePointUpdate,
    ReferencePointRead,
    ReferencePointPrecedenceCreate,
    ReferencePointPrecedenceRead,
    RPPrecedenceUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reference-points", tags=["reference-points"])


# ─────────────────────────────────────────────────────────────────────────────
# Reference Points — CRUD per modello macchina
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/model/{machine_model_id}", response_model=list[ReferencePointRead])
async def list_reference_points(
    machine_model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[ReferencePoint]:
    """Elenca tutti i reference point definiti per un modello macchina.

    Il modello macchina determina la struttura BOM e il DAG delle precedenze.
    Ogni TURBOPRESS-X500 usa gli stessi RP; macchine di modello diverso
    hanno RP diversi (es. TURBOPRESS-X300 ha una struttura BOM diversa).
    """
    result = await db.execute(
        select(ReferencePoint)
        .where(ReferencePoint.machine_model_id == machine_model_id)
        .order_by(ReferencePoint.code)
    )
    return list(result.scalars().all())


@router.post("", response_model=ReferencePointRead, status_code=201)
async def create_reference_point(
    payload: ReferencePointCreate,
    db: AsyncSession = Depends(get_db),
) -> ReferencePoint:
    """Crea un nuovo reference point.

    Il codice (es. "RP-001") deve essere univoco per modello macchina.
    Il target_order_material identifica quale macroaggregato/aggregato
    deve essere completato prima che le operazioni legate a questo RP possano iniziare.
    """
    rp = ReferencePoint(**payload.model_dump())
    db.add(rp)
    await db.commit()
    await db.refresh(rp)
    return rp


@router.patch("/{rp_id}", response_model=ReferencePointRead)
async def update_reference_point(
    rp_id: uuid.UUID,
    payload: ReferencePointUpdate,
    db: AsyncSession = Depends(get_db),
) -> ReferencePoint:
    """Aggiorna nome o target di un reference point."""
    obj = await db.get(ReferencePoint, rp_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Reference point non trovato")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{rp_id}")
async def delete_reference_point(
    rp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Elimina un reference point e tutte le sue precedenze (cascade)."""
    obj = await db.get(ReferencePoint, rp_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Reference point non trovato")
    await db.delete(obj)
    await db.commit()
    return Response(status_code=204)


# ─────────────────────────────────────────────────────────────────────────────
# Precedenze DAG — archi del grafo
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/model/{machine_model_id}/precedences", response_model=list[ReferencePointPrecedenceRead])
async def list_precedences(
    machine_model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[ReferencePointPrecedence]:
    """Restituisce tutti gli archi del DAG per un modello macchina.

    Ogni arco (predecessor_reference_point_id → reference_point_id) indica
    che il RP figlio non può iniziare finché il RP predecessore non è completato.
    Il frontend React Flow usa questi archi per disegnare il grafo visivo
    nella pagina ReferencePointConfig.
    """
    result = await db.execute(
        select(ReferencePointPrecedence)
        .where(ReferencePointPrecedence.machine_model_id == machine_model_id)
    )
    return list(result.scalars().all())


@router.post("/precedences", response_model=ReferencePointPrecedenceRead, status_code=201)
async def create_precedence(
    payload: ReferencePointPrecedenceCreate,
    db: AsyncSession = Depends(get_db),
) -> ReferencePointPrecedence:
    """Aggiunge un arco di precedenza tra due reference point.

    ATTENZIONE: aggiungere un arco potrebbe creare un ciclo nel DAG.
    La validazione dell'aciclicità viene eseguita dal dag_builder.py
    al momento del lancio del solver, non qui (per performance).
    Il frontend implementa la validazione visiva in tempo reale (DFS in TypeScript).
    """
    rpp = ReferencePointPrecedence(**payload.model_dump())
    db.add(rpp)
    await db.commit()
    await db.refresh(rpp)
    return rpp


@router.delete("/precedences/{precedence_id}")
async def delete_precedence(
    precedence_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Rimuove un arco di precedenza dal DAG."""
    obj = await db.get(ReferencePointPrecedence, precedence_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Precedenza non trovata")
    await db.delete(obj)
    await db.commit()
    return Response(status_code=204)


@router.put("/model/{machine_model_id}/precedences/bulk", response_model=list[ReferencePointPrecedenceRead])
async def bulk_update_precedences(
    machine_model_id: uuid.UUID,
    payload: RPPrecedenceUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> list[ReferencePointPrecedence]:
    """Sostituisce l'intero set di precedenze per un modello macchina.

    Processo: DELETE tutte le precedenze esistenti → INSERT le nuove.
    Questo approccio "replace-all" è più semplice da gestire nel frontend
    React Flow, che ricrea l'intero grafo ad ogni salvataggio.

    Chiamato quando il planner clicca "Salva configurazione" nella pagina DAG.
    """
    # Rimuove tutte le precedenze esistenti per questo modello
    await db.execute(
        delete(ReferencePointPrecedence).where(
            ReferencePointPrecedence.machine_model_id == machine_model_id
        )
    )

    # Inserisce le nuove precedenze
    new_rpp: list[ReferencePointPrecedence] = []
    for item in payload.precedences:
        for pred_id in item.predecessor_ids:
            rpp = ReferencePointPrecedence(
                reference_point_id=item.rp_id,
                predecessor_reference_point_id=pred_id,
                machine_model_id=machine_model_id,
            )
            db.add(rpp)
            new_rpp.append(rpp)

    await db.commit()
    for obj in new_rpp:
        await db.refresh(obj)

    logger.info(
        "Bulk update precedenze modello=%s: %d archi inseriti",
        machine_model_id, len(new_rpp),
    )
    return new_rpp
