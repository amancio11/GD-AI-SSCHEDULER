# backend/app/api/routes/dag.py
#
# Endpoint DAG arricchito.
# Mostra il DAG dei Reference Point con etichette parlanti:
#   - descrizione dell'ordine target
#   - elenco delle operazioni vincolate
#   - priorità topologica
#
# GET /api/dag/{machine_order_id}/enriched

from __future__ import annotations

from typing import Any
from uuid import UUID

import networkx as nx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models import (
    MachineOrder,
    Operation,
    ProductionOrder,
    ReferencePoint,
    ReferencePointPrecedence,
    Routing,
)

router = APIRouter(prefix="/api/dag", tags=["dag"])


@router.get("/{machine_order_id}/enriched")
async def get_enriched_dag(
    machine_order_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    mo = await db.get(MachineOrder, machine_order_id)
    if not mo:
        raise HTTPException(404, "MachineOrder non trovato")

    # 1) Tutti i RP del modello macchina
    rp_q = select(ReferencePoint).where(
        ReferencePoint.machine_model_id == mo.machine_model_id
    )
    rp_res = await db.execute(rp_q)
    rps = list(rp_res.scalars().all())

    # 2) Tutte le precedenze
    prec_q = select(ReferencePointPrecedence).where(
        ReferencePointPrecedence.machine_model_id == mo.machine_model_id
    )
    prec_res = await db.execute(prec_q)
    precedences = list(prec_res.scalars().all())

    # 3) Mappa material_code → ProductionOrder + operazioni
    po_q = (
        select(ProductionOrder)
        .where(ProductionOrder.machine_order_id == machine_order_id)
        .options(
            selectinload(ProductionOrder.routing).selectinload(Routing.operations)
        )
    )
    po_res = await db.execute(po_q)
    orders = list(po_res.scalars().all())
    order_by_material: dict[str, ProductionOrder] = {
        o.material_code: o for o in orders
    }

    # 4) Calcola priorità topologica
    G = nx.DiGraph()
    for rp in rps:
        G.add_node(str(rp.id))
    for prec in precedences:
        G.add_edge(
            str(prec.predecessor_reference_point_id),
            str(prec.reference_point_id),
        )

    if not nx.is_directed_acyclic_graph(G):
        raise HTTPException(
            500,
            "Il DAG dei Reference Point contiene cicli: impossibile calcolare le priorità.",
        )

    # Generation index = posizione nel sort topologico per livelli
    generations = list(nx.topological_generations(G))
    priority_by_node: dict[str, int] = {}
    rank = 1
    for gen in generations:
        # Stabilizza l'ordine all'interno di una generation per codice RP
        sorted_gen = sorted(
            gen, key=lambda nid: next((r.code for r in rps if str(r.id) == nid), "")
        )
        for nid in sorted_gen:
            priority_by_node[nid] = rank
            rank += 1

    # 5) Costruisci payload nodi
    nodes_payload: list[dict[str, Any]] = []
    for rp in rps:
        target_order = order_by_material.get(rp.target_order_material)
        if target_order is None:
            # RP punta a un materiale che non è nella BOM di questo machine_order:
            # capita se il modello è condiviso ma l'ordine ne usa solo una parte.
            # Lo includiamo ma marcato come vuoto.
            nodes_payload.append(
                {
                    "id": str(rp.id),
                    "rp_code": rp.code,
                    "rp_label": rp.name,
                    "target_order_material": rp.target_order_material,
                    "target_order_description": "(non presente in questo ordine)",
                    "target_level": rp.target_level.value
                    if hasattr(rp.target_level, "value")
                    else str(rp.target_level),
                    "operations_count": 0,
                    "operations": [],
                    "priority_rank": priority_by_node.get(str(rp.id), 0),
                }
            )
            continue

        ops_list: list[dict[str, str]] = []
        if target_order.routing:
            for op in sorted(
                target_order.routing.operations, key=lambda x: x.sequence_number
            ):
                ops_list.append(
                    {"id": str(op.id), "description": op.description}
                )

        nodes_payload.append(
            {
                "id": str(rp.id),
                "rp_code": rp.code,
                "rp_label": rp.name,
                "target_order_material": target_order.material_code,
                "target_order_description": target_order.description,
                "target_level": rp.target_level.value
                if hasattr(rp.target_level, "value")
                else str(rp.target_level),
                "operations_count": len(ops_list),
                "operations": ops_list,
                "priority_rank": priority_by_node.get(str(rp.id), 0),
            }
        )

    edges_payload = [
        {
            "from": str(prec.predecessor_reference_point_id),
            "to": str(prec.reference_point_id),
        }
        for prec in precedences
    ]

    return {
        "nodes": nodes_payload,
        "edges": edges_payload,
    }