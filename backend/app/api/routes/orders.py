"""Router: Ordini di produzione e BOM (Bill of Materials).

Questo modulo gestisce l'accesso alla gerarchia degli ordini SAP:
  - MachineOrder (radice): rappresenta la macchina fisica da montare
  - ProductionOrder: tutti i livelli sotto la macchina (macroaggregati, aggregati, gruppi, componenti)
  - BOM tree: vista ad albero ricorsiva della gerarchia completa
  - Operations: le operazioni di lavorazione legate al routing di un ordine

Il pattern di accesso è sempre discendente dalla macchina verso il basso,
garantendo l'isolamento per ordine macchina (multi-tenancy light).
"""
from __future__ import annotations

import uuid
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.machine import MachineOrder, MachineModel
from app.models.production import ProductionOrder
from app.models.routing import Routing, Operation
from app.schemas.production import (
    ProductionOrderRead,
    ProductionOrderUpdate,
    BOMTreeNode,
)
from app.schemas.machine import MachineOrderRead
from app.schemas.routing import OperationRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["orders"])


# ─────────────────────────────────────────────────────────────────────────────
# Machine Orders
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/machines", response_model=list[MachineOrderRead])
async def list_machine_orders(
    db: AsyncSession = Depends(get_db),
) -> list[MachineOrder]:
    """Elenca tutti gli ordini macchina nel sistema.

    Ogni ordine macchina corrisponde a una TURBOPRESS (o macchina analoga)
    che deve essere assemblata. È il nodo radice dell'intera gerarchia BOM.
    """
    result = await db.execute(select(MachineOrder).order_by(MachineOrder.created_at.desc()))
    return list(result.scalars().all())


@router.get("/machines/{machine_order_id}", response_model=MachineOrderRead)
async def get_machine_order(
    machine_order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> MachineOrder:
    """Restituisce un singolo ordine macchina per ID."""
    obj = await db.get(MachineOrder, machine_order_id)
    if not obj:
        raise HTTPException(status_code=404, detail="MachineOrder non trovato")
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# BOM Tree — vista gerarchica completa
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/machine/{machine_order_id}/bom-tree", response_model=BOMTreeNode)
async def get_bom_tree(
    machine_order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> BOMTreeNode:
    """Costruisce e restituisce l'albero BOM completo di una macchina.

    Processo:
    1. Carica tutti i ProductionOrder legati alla machine_order_id con una sola query.
    2. Costruisce un dict {id → nodo} per lookup O(1).
    3. Collega ogni nodo al proprio padre tramite parent_order_id.
    4. Restituisce il nodo radice (livello MACHINE) con i figli innestati.

    Questo approccio evita il classico problema N+1 query della navigazione ricorsiva.
    """
    machine = await db.get(MachineOrder, machine_order_id)
    if not machine:
        raise HTTPException(status_code=404, detail="MachineOrder non trovato")

    # Carica tutti gli ordini della macchina in una sola query (lazy='selectin' già attivo)
    result = await db.execute(
        select(ProductionOrder)
        .where(ProductionOrder.machine_order_id == machine_order_id)
    )
    orders = list(result.scalars().all())

    # Costruisce la mappa id → BOMTreeNode (ancora senza figli)
    nodes: dict[uuid.UUID, BOMTreeNode] = {}
    for po in orders:
        nodes[po.id] = BOMTreeNode(
            id=po.id,
            sap_order_id=po.sap_order_id,
            material_code=po.material_code,
            description=po.description,
            level=po.level,
            status=po.status,
            progress_pct=po.progress_pct,
            workcenter_id=po.workcenter_id,
            is_purchase_component=po.is_purchase_component,
            is_production_component_untracked=po.is_production_component_untracked,
            missing_arrival_date=po.missing_arrival_date,
            children=[],
        )

    # Collega figli ai rispettivi padri; i nodi senza padre sono radici
    root: BOMTreeNode | None = None
    for po in orders:
        node = nodes[po.id]
        if po.parent_order_id and po.parent_order_id in nodes:
            nodes[po.parent_order_id].children.append(node)
        else:
            # Il nodo senza padre (o con padre fuori dalla macchina) è la radice MACHINE
            root = node

    if root is None:
        raise HTTPException(status_code=404, detail="Nodo radice BOM non trovato")

    return root


# ─────────────────────────────────────────────────────────────────────────────
# Production Orders — CRUD singolo ordine
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{order_id}", response_model=ProductionOrderRead)
async def get_production_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ProductionOrder:
    """Recupera un ordine di produzione per ID (qualsiasi livello BOM)."""
    obj = await db.get(ProductionOrder, order_id)
    if not obj:
        raise HTTPException(status_code=404, detail="ProductionOrder non trovato")
    return obj


@router.patch("/{order_id}", response_model=ProductionOrderRead)
async def update_production_order(
    order_id: uuid.UUID,
    payload: ProductionOrderUpdate,
    db: AsyncSession = Depends(get_db),
) -> ProductionOrder:
    """Aggiorna campi di un ordine di produzione (es. stato, avanzamento).

    Questo endpoint viene chiamato dal frontend quando un operatore aggiorna
    manualmente il progresso di un ordine. Il campo progress_pct indica la
    percentuale di completamento del montaggio (0-100).
    """
    obj = await db.get(ProductionOrder, order_id)
    if not obj:
        raise HTTPException(status_code=404, detail="ProductionOrder non trovato")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)

    await db.commit()
    await db.refresh(obj)
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# Operations — operazioni di lavorazione di un ordine
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{order_id}/operations", response_model=list[OperationRead])
async def get_order_operations(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[Operation]:
    """Recupera le operazioni di lavorazione per un ordine di produzione.

    Il routing è la sequenza di operazioni che gli operatori devono eseguire
    per completare quell'ordine. In modalità SIMULTANEOUS tutte le operazioni
    possono essere lavorate in parallelo da operatori diversi.
    """
    # Prima verifica che esista il routing per questo ordine
    routing_result = await db.execute(
        select(Routing).where(Routing.production_order_id == order_id)
    )
    routing = routing_result.scalar_one_or_none()
    if not routing:
        return []  # I componenti puri non hanno routing → lista vuota

    result = await db.execute(
        select(Operation)
        .where(Operation.routing_id == routing.id)
        .order_by(Operation.sequence_number)
    )
    return list(result.scalars().all())
