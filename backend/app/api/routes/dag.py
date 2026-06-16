"""Router: DAG completo con operazioni per visualizzazione frontend.

Restituisce il grafo completo degli ordini di produzione con le loro operazioni,
reference point e precedenze — arricchito di info per il React Flow viewer.

Endpoint:
  GET /api/dag/machine/{machine_order_id}/full
    → DAGFullResponse con nodi (ordini+operazioni) e archi (precedenze RP + BOM)
"""
from __future__ import annotations

import uuid
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.production import ProductionOrder
from app.models.machine import MachineOrder
from app.models.routing import Routing, Operation
from app.models.reference import ReferencePoint, ReferencePointPrecedence

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dag", tags=["dag"])


# ── Schemi risposta ───────────────────────────────────────────────────────────

class DAGOperation(BaseModel):
    id: str
    sap_operation_id: str | None
    description: str | None
    operation_type: str
    planned_duration_minutes: int
    progress_pct: float
    status: str
    reference_point_id: str | None
    reference_point_code: str | None  # es. "RP-MA1-01"
    workcenter_id: str | None


class DAGOrder(BaseModel):
    id: str
    sap_order_id: str
    description: str | None
    level: str                     # MACHINE | MACROAGGREGATE | AGGREGATE | GROUP | COMPONENT
    material_code: str | None
    progress_pct: float
    status: str
    parent_order_id: str | None
    workcenter_id: str | None
    operations: list[DAGOperation]


class DAGEdge(BaseModel):
    id: str
    source: str                    # order_id del predecessore
    target: str                    # order_id del successore
    edge_type: str                 # "BOM_PARENT" | "RP_PRECEDENCE"
    rp_predecessor_code: str | None
    rp_successor_code: str | None
    label: str | None
    # ── NUOVI CAMPI SEMANTICI ──
    semantic_label: str | None     # Es: "Op 'Montaggio idraulico' attende completamento 'Struttura Portante'"
    blocked_operation_id: str | None       # ID dell'operazione bloccata (quella con il RP)
    blocked_operation_desc: str | None     # Descrizione operazione bloccata
    blocking_order_id: str | None          # ID dell'ordine che deve completarsi
    blocking_order_desc: str | None        # Descrizione ordine bloccante
    parent_order_id: str | None            # Ordine padre che contiene l'operazione bloccata
    parent_order_desc: str | None          # Descrizione ordine padre


class DAGFullResponse(BaseModel):
    machine_order_id: str
    machine_description: str | None
    orders: list[DAGOrder]
    edges: list[DAGEdge]
    # Mappa rp_id → info RP per lookup rapido nel frontend
    reference_points: dict[str, dict[str, Any]]


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/machine/{machine_order_id}/full", response_model=DAGFullResponse)
async def get_full_dag(
    machine_order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DAGFullResponse:
    """Restituisce il DAG completo con ordini, operazioni e archi di precedenza.

    Struttura del grafo:
    - Nodi: ogni ProductionOrder diventa un nodo contenente la lista delle sue operazioni
    - Archi BOM_PARENT: figlio → padre (relazione gerarchia BOM)
    - Archi RP_PRECEDENCE: target_RP_pred → target_RP_succ (vincoli del DAG RP)

    Il frontend React Flow usa:
    - Nodi a forma di card con lista operazioni interna
    - Archi colorati per tipo (BOM=grigio, RP=arancione/rosso)
    - Layout gerarchico automatico (top-down per livello BOM)
    """
    # 1. Carica machine_order
    machine = await db.get(MachineOrder, machine_order_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine order non trovato")

    # 2. Carica tutti gli ordini di produzione di questa macchina
    po_result = await db.execute(
        select(ProductionOrder).where(
            ProductionOrder.machine_order_id == machine_order_id
        ).order_by(ProductionOrder.level, ProductionOrder.sap_order_id)
    )
    orders_db = list(po_result.scalars().all())

    if not orders_db:
        return DAGFullResponse(
            machine_order_id=str(machine_order_id),
            machine_description=machine.description,
            orders=[],
            edges=[],
            reference_points={},
        )

    order_ids = [o.id for o in orders_db]

    # 3. Carica tutti i routing per questi ordini (map: production_order_id → routing_id)
    routing_result = await db.execute(
        select(Routing).where(Routing.production_order_id.in_(order_ids))
    )
    routings_by_po: dict[uuid.UUID, uuid.UUID] = {
        r.production_order_id: r.id for r in routing_result.scalars().all()
    }

    # 4. Carica tutte le operazioni per questi routing
    routing_ids = list(routings_by_po.values())
    ops_result = await db.execute(
        select(Operation).where(Operation.routing_id.in_(routing_ids))
        .order_by(Operation.sequence_number)
    ) if routing_ids else None

    ops_by_routing: dict[uuid.UUID, list[Operation]] = {}
    if ops_result:
        for op in ops_result.scalars().all():
            ops_by_routing.setdefault(op.routing_id, []).append(op)

    # 5. Carica reference points per questo machine_model
    rp_result = await db.execute(
        select(ReferencePoint).where(
            ReferencePoint.machine_model_id == machine.machine_model_id
        )
    )
    rps = {rp.id: rp for rp in rp_result.scalars().all()}

    # 6. Carica precedenze RP
    prec_result = await db.execute(
        select(ReferencePointPrecedence).where(
            ReferencePointPrecedence.machine_model_id == machine.machine_model_id
        )
    )
    rp_precedences = list(prec_result.scalars().all())

    # 7. Costruisci mappa material_code → order per risolvere i target RP
    material_to_order: dict[str, ProductionOrder] = {
        o.material_code: o for o in orders_db if o.material_code
    }

    # 8. Assembla nodi DAGOrder
    dag_orders: list[DAGOrder] = []
    for po in orders_db:
        routing_id = routings_by_po.get(po.id)
        operations_db = ops_by_routing.get(routing_id, []) if routing_id else []

        dag_ops = []
        for op in operations_db:
            rp = rps.get(op.reference_point_id) if op.reference_point_id else None
            dag_ops.append(DAGOperation(
                id=str(op.id),
                sap_operation_id=op.sap_operation_id,
                description=op.description,
                operation_type=op.operation_type.value if hasattr(op.operation_type, 'value') else str(op.operation_type),
                planned_duration_minutes=op.planned_duration_minutes or 0,
                progress_pct=op.progress_pct or 0.0,
                status=op.status.value if hasattr(op.status, 'value') else str(op.status),
                reference_point_id=str(op.reference_point_id) if op.reference_point_id else None,
                reference_point_code=rp.code if rp else None,
                workcenter_id=str(op.workcenter_id) if op.workcenter_id else None,
            ))

        dag_orders.append(DAGOrder(
            id=str(po.id),
            sap_order_id=po.sap_order_id,
            description=po.description,
            level=po.level.value if hasattr(po.level, 'value') else str(po.level),
            material_code=po.material_code,
            progress_pct=po.progress_pct or 0.0,
            status=po.status.value if hasattr(po.status, 'value') else str(po.status),
            parent_order_id=str(po.parent_order_id) if po.parent_order_id else None,
            workcenter_id=str(po.workcenter_id) if po.workcenter_id else None,
            operations=dag_ops,
        ))

    # 9. Costruisci archi
    # 9. Costruisci archi DAG — BOM + RP con SEMANTICA CHIARA
    dag_edges: list[DAGEdge] = []
    edge_counter = 0
 
    # 9a. Archi BOM (parent-child)
    for po in orders_db:
        if po.parent_order_id:
            edge_counter += 1
            parent_po = next((o for o in orders_db if o.id == po.parent_order_id), None)
            dag_edges.append(DAGEdge(
                id=f"bom-{edge_counter}",
                source=str(po.id),
                target=str(po.parent_order_id),
                edge_type="BOM_PARENT",
                rp_predecessor_code=None,
                rp_successor_code=None,
                label=None,
                semantic_label=None,
                blocked_operation_id=None,
                blocked_operation_desc=None,
                blocking_order_id=None,
                blocking_order_desc=None,
                parent_order_id=None,
                parent_order_desc=None,
            ))
 
    # 9b. Archi RP_PRECEDENCE con semantica completa
    #
    # Per ogni arco nel DAG dei reference point (pred_rp → succ_rp):
    #   - L'operazione del PADRE che ha reference_point_id = succ_rp 
    #     è BLOCCATA da tutti gli ordini target di pred_rp (e figli)
    #   - Ma nel DAG visivo l'arco è tra gli ORDINI TARGET dei due RP
    #
    # La semantica è: "l'ordine target di pred_rp deve completarsi
    #   PRIMA che l'operazione con succ_rp possa iniziare"
 
    # Mappa: rp_id → operazioni che lo referenziano (con info sull'ordine padre)
    rp_to_operations: dict[uuid.UUID, list[tuple[Operation, ProductionOrder]]] = {}
    for po in orders_db:
        routing_id = routings_by_po.get(po.id)
        if not routing_id:
            continue
        for op in ops_by_routing.get(routing_id, []):
            if op.reference_point_id:
                rp_to_operations.setdefault(op.reference_point_id, []).append((op, po))
 
    for prec in rp_precedences:
        pred_rp = rps.get(prec.predecessor_reference_point_id)
        succ_rp = rps.get(prec.reference_point_id)
        if not pred_rp or not succ_rp:
            continue
 
        # Ordini target dei due RP
        pred_target_order = material_to_order.get(pred_rp.target_order_material or "")
        succ_target_order = material_to_order.get(succ_rp.target_order_material or "")
        if not pred_target_order or not succ_target_order:
            continue
 
        # Operazione bloccata: quella dell'ordine PADRE che ha reference_point_id = succ_rp
        blocked_ops = rp_to_operations.get(succ_rp.id, [])
        blocked_op_desc = None
        blocked_op_id = None
        parent_order_desc = None
        parent_order_id = None
        if blocked_ops:
            op, parent_po = blocked_ops[0]  # tipicamente 1 operazione per RP
            blocked_op_id = str(op.id)
            blocked_op_desc = op.description
            parent_order_id = str(parent_po.id)
            parent_order_desc = parent_po.description
 
        # Label semantica leggibile
        semantic = (
            f"Op '{blocked_op_desc or '?'}' di '{parent_order_desc or '?'}' "
            f"attende completamento di '{pred_target_order.description or pred_rp.target_order_material}'"
        )
 
        edge_counter += 1
        dag_edges.append(DAGEdge(
            id=f"rp-{edge_counter}",
            source=str(pred_target_order.id),
            target=str(succ_target_order.id),
            edge_type="RP_PRECEDENCE",
            rp_predecessor_code=pred_rp.code,
            rp_successor_code=succ_rp.code,
            label=f"{pred_rp.code} → {succ_rp.code}",
            semantic_label=semantic,
            blocked_operation_id=blocked_op_id,
            blocked_operation_desc=blocked_op_desc,
            blocking_order_id=str(pred_target_order.id),
            blocking_order_desc=pred_target_order.description,
            parent_order_id=parent_order_id,
            parent_order_desc=parent_order_desc,
        ))

    # 10. Mappa reference_points arricchita
    rp_info: dict[str, dict[str, Any]] = {}
    for rp_id, rp in rps.items():
        target_order = material_to_order.get(rp.target_order_material or "")
        linked_ops = rp_to_operations.get(rp_id, [])
        
        rp_info[str(rp_id)] = {
            "code": rp.code,
            "name": rp.name,
            "target_level": rp.target_level.value if hasattr(rp.target_level, 'value') else str(rp.target_level),
            "target_order_material": rp.target_order_material,
            "target_order_id": str(target_order.id) if target_order else None,
            "target_order_description": target_order.description if target_order else None,
            # NUOVO: quali operazioni sono vincolate da questo RP
            "linked_operations": [
                {
                    "operation_id": str(op.id),
                    "operation_desc": op.description,
                    "parent_order_id": str(po.id),
                    "parent_order_sap_id": po.sap_order_id,
                    "parent_order_desc": po.description,
                }
                for op, po in linked_ops
            ],
            # NUOVO: semantica leggibile
            "semantic": (
                f"Vincola op di '{linked_ops[0][1].description}': "
                f"non iniziare finché '{target_order.description if target_order else rp.target_order_material}' non è completo"
            ) if linked_ops and target_order else (
                f"Punta a '{target_order.description if target_order else rp.target_order_material}'"
            ),
        }

    return DAGFullResponse(
        machine_order_id=str(machine_order_id),
        machine_description=machine.description,
        orders=dag_orders,
        edges=dag_edges,
        reference_points=rp_info,
    )


# ── Endpoint arricchito per DAGViewerEnhanced ─────────────────────────────────

@router.get("/{machine_order_id}/enriched")
async def get_enriched_dag(
    machine_order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Restituisce il DAG dei Reference Point arricchito per DAGViewerEnhanced.

    Include priorità topologica, descrizione ordine target e lista operazioni
    vincolate. Usato da GET /api/dag/{machine_order_id}/enriched.
    """
    import networkx as nx

    mo = await db.get(MachineOrder, machine_order_id)
    if not mo:
        raise HTTPException(404, "MachineOrder non trovato")

    # 1. Tutti i RP del modello macchina
    rp_q = select(ReferencePoint).where(
        ReferencePoint.machine_model_id == mo.machine_model_id
    )
    rp_res = await db.execute(rp_q)
    rps_list = list(rp_res.scalars().all())

    # 2. Tutte le precedenze
    prec_q = select(ReferencePointPrecedence).where(
        ReferencePointPrecedence.machine_model_id == mo.machine_model_id
    )
    prec_res = await db.execute(prec_q)
    precedences = list(prec_res.scalars().all())

    # 3. Mappa material_code → ProductionOrder + operazioni
    po_q = select(ProductionOrder).where(
        ProductionOrder.machine_order_id == machine_order_id
    )
    po_res = await db.execute(po_q)
    orders_list = list(po_res.scalars().all())
    order_by_material: dict[str, ProductionOrder] = {
        o.material_code: o for o in orders_list if o.material_code
    }

    # 4. Carica routings e operations
    order_ids = [o.id for o in orders_list]
    routing_result = await db.execute(
        select(Routing).where(Routing.production_order_id.in_(order_ids))
    )
    routings_by_po_id: dict[uuid.UUID, uuid.UUID] = {
        r.production_order_id: r.id for r in routing_result.scalars().all()
    }
    routing_ids = list(routings_by_po_id.values())
    ops_by_routing_id: dict[uuid.UUID, list[Operation]] = {}
    if routing_ids:
        ops_result = await db.execute(
            select(Operation).where(Operation.routing_id.in_(routing_ids))
            .order_by(Operation.sequence_number)
        )
        for op in ops_result.scalars().all():
            ops_by_routing_id.setdefault(op.routing_id, []).append(op)

    # 5. Calcola priorità topologica via networkx
    G = nx.DiGraph()
    for rp in rps_list:
        G.add_node(str(rp.id))
    for prec in precedences:
        G.add_edge(
            str(prec.predecessor_reference_point_id),
            str(prec.reference_point_id),
        )

    if not nx.is_directed_acyclic_graph(G):
        raise HTTPException(500, "Il DAG dei Reference Point contiene cicli.")

    generations = list(nx.topological_generations(G))
    priority_by_node: dict[str, int] = {}
    rank = 1
    for gen in generations:
        sorted_gen = sorted(
            gen, key=lambda nid: next((r.code for r in rps_list if str(r.id) == nid), "")
        )
        for nid in sorted_gen:
            priority_by_node[nid] = rank
            rank += 1

    # 6. Costruisci payload nodi
    nodes_payload: list[dict[str, Any]] = []
    for rp in rps_list:
        target_order = order_by_material.get(rp.target_order_material or "")
        ops_list: list[dict[str, str]] = []
        if target_order:
            routing_id = routings_by_po_id.get(target_order.id)
            if routing_id:
                for op in ops_by_routing_id.get(routing_id, []):
                    ops_list.append({"id": str(op.id), "description": op.description or ""})

        nodes_payload.append({
            "id": str(rp.id),
            "rp_code": rp.code,
            "rp_label": rp.name,
            "target_order_material": rp.target_order_material or "",
            "target_order_description": (
                target_order.description
                if target_order
                else "(non presente in questo ordine)"
            ),
            "target_level": (
                rp.target_level.value if hasattr(rp.target_level, "value") else str(rp.target_level)
            ),
            "operations_count": len(ops_list),
            "operations": ops_list,
            "priority_rank": priority_by_node.get(str(rp.id), 0),
        })

    edges_payload = [
        {
            "from": str(prec.predecessor_reference_point_id),
            "to": str(prec.reference_point_id),
        }
        for prec in precedences
    ]

    return {"nodes": nodes_payload, "edges": edges_payload}