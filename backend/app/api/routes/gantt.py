# backend/app/api/routes/gantt.py
#
# Endpoint Gantt arricchito: ritorna entries pronte per il rendering avanzato
# del frontend, con dipendenze derivate dal DAG RP, marker RP e flag critical path.
#
# GET /api/gantt/{scenario_id}
#   â†’ { entries: [...], dependencies: [...], rp_markers: [...] }

from __future__ import annotations

from typing import Any
from uuid import UUID

import networkx as nx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.machine import MachineOrder
from app.models.missing import MissingComponent
from app.models.operator import Operator
from app.models.production import ProductionOrder
from app.models.reference import ReferencePoint, ReferencePointPrecedence
from app.models.routing import Operation, Routing
from app.models.schedule import ScheduleEntry, ScheduleScenario
from app.models.workcenter import Workcenter

router = APIRouter(prefix="/gantt", tags=["gantt"])


@router.get("/{scenario_id}")
async def get_enriched_gantt(
    scenario_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # 1) Verifica scenario
    scenario = await db.get(ScheduleScenario, scenario_id)
    if not scenario:
        raise HTTPException(404, "Scenario non trovato")

    # 2) Carica tutte le entries con le relazioni necessarie
    q = (
        select(ScheduleEntry)
        .where(ScheduleEntry.scenario_id == scenario_id)
        .options(
            selectinload(ScheduleEntry.operation)
            .selectinload(Operation.routing)
            .selectinload(Routing.production_order)
            .selectinload(ProductionOrder.workcenter),
            selectinload(ScheduleEntry.operation).selectinload(Operation.workcenter),
            selectinload(ScheduleEntry.operation).selectinload(
                Operation.reference_point
            ),
            selectinload(ScheduleEntry.operator).selectinload(Operator.workcenter),
        )
    )
    res = await db.execute(q)
    entries = list(res.scalars().all())

    if not entries:
        return {"entries": [], "dependencies": [], "rp_markers": []}

    # 3) Mancanti per ordine (per badge sul Gantt)
    missing_q = select(MissingComponent).where(
        MissingComponent.is_arrived.is_(False)
    )
    missing_res = await db.execute(missing_q)
    missing_by_order: dict[UUID, list[str]] = {}
    for m in missing_res.scalars().all():
        missing_by_order.setdefault(m.production_order_id, []).append(
            m.component_material
        )

    # 4) Serializza entries
    entries_payload: list[dict[str, Any]] = []
    for e in entries:
        op = e.operation
        po = op.routing.production_order
        wc = op.workcenter or po.workcenter
        operator = e.operator
        entries_payload.append(
            {
                "id": str(e.id),
                "operation_id": str(op.id),
                "operation_description": op.description,
                "operation_type": op.operation_type.value
                if hasattr(op.operation_type, "value")
                else str(op.operation_type),
                "operator_id": str(operator.id),
                "operator_name": operator.full_name,
                "operator_skill": operator.skill.value
                if hasattr(operator.skill, "value")
                else str(operator.skill),
                "workcenter_id": str(wc.id),
                "workcenter_code": wc.code,
                "workcenter_name": wc.name,
                "production_order_id": str(po.id),
                "production_order_material": po.material_code,
                "production_order_description": po.description,
                "production_order_level": po.level.value
                if hasattr(po.level, "value")
                else str(po.level),
                "parent_order_id": str(po.parent_order_id) if po.parent_order_id else None,
                "scheduled_start": e.scheduled_start.isoformat() if e.scheduled_start else None,
                "scheduled_end": e.scheduled_end.isoformat() if e.scheduled_end else None,
                "actual_start": e.actual_start.isoformat() if e.actual_start else None,
                "actual_end": e.actual_end.isoformat() if e.actual_end else None,
                "status": e.status.value if hasattr(e.status, "value") else str(e.status),
                "progress_pct": float(op.progress_pct or 0.0),
                "is_critical_path": False,  # popolato sotto
                "missing_components": missing_by_order.get(po.id, []),
                "reference_point_code": (
                    op.reference_point.code if op.reference_point else None
                ),
            }
        )

    # 5) Costruisci grafo precedenze per critical path
    #    Vertici = entries, archi = (a â†’ b) se end(a) <= start(b) ed esiste vincolo
    #    Per semplicitÃ  qui usiamo solo gli archi RP DAG (lo stesso che genera
    #    `dependencies` sotto). Per il critical path "vero" servirebbe il modello
    #    di scheduling completo â€” questo Ã¨ una buona approssimazione visiva.

    # Recupera RP DAG del machine_order corrispondente
    mo_id = scenario.machine_order_id
    # Trovo il machine_model attraverso il machine_order
    # MachineOrder already imported above
    mo = await db.get(MachineOrder, mo_id)
    if not mo:
        return {"entries": entries_payload, "dependencies": [], "rp_markers": []}

    rp_q = select(ReferencePoint).where(
        ReferencePoint.machine_model_id == mo.machine_model_id
    )
    rp_res = await db.execute(rp_q)
    rps = {rp.id: rp for rp in rp_res.scalars().all()}

    prec_q = select(ReferencePointPrecedence).where(
        ReferencePointPrecedence.machine_model_id == mo.machine_model_id
    )
    prec_res = await db.execute(prec_q)
    precedences = list(prec_res.scalars().all())

    # 6) Mappa: production_order_id â†’ set di entries del sottoalbero BOM
    #    Costruisco l'albero ordini, poi per ogni RP target trovo le entries
    all_orders_q = select(ProductionOrder).where(
        ProductionOrder.machine_order_id == mo_id
    )
    all_orders_res = await db.execute(all_orders_q)
    all_orders = list(all_orders_res.scalars().all())
    children: dict[UUID, list[UUID]] = {}
    for o in all_orders:
        if o.parent_order_id:
            children.setdefault(o.parent_order_id, []).append(o.id)

    def descendants(root_id: UUID) -> set[UUID]:
        result = {root_id}
        stack = [root_id]
        while stack:
            current = stack.pop()
            for c in children.get(current, []):
                if c not in result:
                    result.add(c)
                    stack.append(c)
        return result

    # Mappa material_code â†’ order_id (per target_order_material dei RP)
    material_to_id: dict[str, UUID] = {
        o.material_code: o.id for o in all_orders
    }

    # Mappa: entry_id â†’ operation_id â†’ production_order_id
    entries_by_order: dict[UUID, list[ScheduleEntry]] = {}
    for e in entries:
        po_id = e.operation.routing.production_order_id
        entries_by_order.setdefault(po_id, []).append(e)

    # 7) Calcola dipendenze per ogni arco del DAG RP
    dependencies_payload: list[dict[str, str]] = []
    rp_markers_payload: list[dict[str, Any]] = []

    for prec in precedences:
        pred_rp = rps.get(prec.predecessor_reference_point_id)
        succ_rp = rps.get(prec.reference_point_id)
        if not pred_rp or not succ_rp:
            continue
        pred_order_id = material_to_id.get(pred_rp.target_order_material)
        succ_order_id = material_to_id.get(succ_rp.target_order_material)
        if not pred_order_id or not succ_order_id:
            continue

        # Entries del sotto-albero del predecessore (tutte le op ricorsive)
        pred_subtree = descendants(pred_order_id)
        succ_subtree = descendants(succ_order_id)

        pred_entries = [
            e
            for oid in pred_subtree
            for e in entries_by_order.get(oid, [])
        ]
        succ_entries = [
            e
            for oid in succ_subtree
            for e in entries_by_order.get(oid, [])
        ]

        if not pred_entries or not succ_entries:
            continue

        # Trova entry "last" del predecessor (max end) e "first" del successor (min start)
        last_pred = max(pred_entries, key=lambda x: x.scheduled_end or x.scheduled_start)
        first_succ = min(succ_entries, key=lambda x: x.scheduled_start or x.scheduled_end)

        dependencies_payload.append(
            {
                "from_entry_id": str(last_pred.id),
                "to_entry_id": str(first_succ.id),
                "source": "RP_DAG",
            }
        )

        # RP marker = momento di completamento del predecessor
        rp_markers_payload.append(
            {
                "entry_id": str(last_pred.id),
                "rp_code": pred_rp.code,
                "rp_label": pred_rp.name,
                "completion_time": (last_pred.scheduled_end or last_pred.scheduled_start).isoformat(),
            }
        )

    # 8) Critical path: longest path nel DAG (entries) pesato con durata
    if dependencies_payload:
        G = nx.DiGraph()
        for e_payload in entries_payload:
            start = e_payload["scheduled_start"]
            end = e_payload["scheduled_end"]
            if not start or not end:
                continue
            from datetime import datetime
            dur = (
                datetime.fromisoformat(end) - datetime.fromisoformat(start)
            ).total_seconds() / 60.0
            G.add_node(e_payload["id"], duration=dur)
        for d in dependencies_payload:
            if G.has_node(d["from_entry_id"]) and G.has_node(d["to_entry_id"]):
                G.add_edge(d["from_entry_id"], d["to_entry_id"])
        if G.number_of_nodes() > 0 and nx.is_directed_acyclic_graph(G):
            try:
                critical_ids = set(nx.dag_longest_path(G, weight="duration"))
            except Exception:
                critical_ids = set()
            for e_payload in entries_payload:
                if e_payload["id"] in critical_ids:
                    e_payload["is_critical_path"] = True

    return {
        "entries": entries_payload,
        "dependencies": dependencies_payload,
        "rp_markers": rp_markers_payload,
    }
