"""DAG Builder — Phase 1 of the CP-SAT scheduling pipeline.

Builds a directed acyclic graph of reference-point precedences and
derives the topological scheduling order for production orders.

Constraints:
- At most 2 DB round-trips (one for reference_points, one for precedences).
- All DB access is async.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import networkx as nx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scheduler.exceptions import CyclicDependencyError
from app.enums import ProductionOrderLevel

if TYPE_CHECKING:
    pass


@dataclass
class SchedulingNode:
    """One entry in the topologically-sorted scheduling plan."""

    rp_id: uuid.UUID
    production_order_id: uuid.UUID
    level: ProductionOrderLevel
    priority_rank: int  # 0 = highest priority (root nodes)


# ─── Public API ───────────────────────────────────────────────────────────────

async def build_precedence_dag(
    machine_model_id: uuid.UUID,
    db: AsyncSession,
) -> nx.DiGraph:
    """Build and validate the reference-point precedence DAG.

    Nodes  : reference_point.id (UUID)
    Edges  : predecessor_id → successor_id
             i.e. A → B means "A must be completed before B can start"

    Raises:
        CyclicDependencyError: if the graph contains a cycle.
    """
    from app.models.reference import ReferencePoint, ReferencePointPrecedence  # local to avoid circulars

    # Query 1 — all reference points for this model
    rp_rows = (
        await db.execute(
            select(ReferencePoint.id, ReferencePoint.code).where(
                ReferencePoint.machine_model_id == machine_model_id
            )
        )
    ).all()

    # Query 2 — all precedence edges for this model
    prec_rows = (
        await db.execute(
            select(
                ReferencePointPrecedence.predecessor_reference_point_id,
                ReferencePointPrecedence.reference_point_id,
            ).where(
                ReferencePointPrecedence.machine_model_id == machine_model_id
            )
        )
    ).all()

    dag: nx.DiGraph = nx.DiGraph()

    # Add nodes (ensures isolated nodes are included)
    for rp_id, _ in rp_rows:
        dag.add_node(rp_id)

    # Add edges: predecessor → successor
    for pred_id, succ_id in prec_rows:
        dag.add_edge(pred_id, succ_id)

    validate_dag(dag)
    return dag


def validate_dag(dag: nx.DiGraph) -> None:
    """Raise CyclicDependencyError if *dag* contains a cycle.

    Uses networkx.find_cycle which returns the list of edges forming
    the cycle in traversal order.
    """
    try:
        cycle = nx.find_cycle(dag, orientation="original")
        # cycle is a list of (u, v, direction) triples
        edges = [(u, v) for u, v, *_ in cycle]
        raise CyclicDependencyError(edges)
    except nx.NetworkXNoCycle:
        pass  # No cycle — DAG is valid


async def get_scheduling_order(
    dag: nx.DiGraph,
    db: AsyncSession,
) -> list[SchedulingNode]:
    """Return production orders in topological precedence order.

    For each reference point in topological sort order the function
    resolves the associated production_order via
    ``reference_point.target_order_material == production_order.material_code``.

    Args:
        dag: Validated precedence DiGraph (output of build_precedence_dag).
        db:  Async database session.

    Returns:
        List of SchedulingNode ordered from highest to lowest priority.
        Empty list if the DAG has no nodes.
    """
    if dag.number_of_nodes() == 0:
        return []

    from app.models.production import ProductionOrder
    from app.models.reference import ReferencePoint

    rp_ids = list(dag.nodes())

    # Single query: load all relevant reference points with their material targets
    rp_rows = (
        await db.execute(
            select(
                ReferencePoint.id,
                ReferencePoint.target_order_material,
                ReferencePoint.target_level,
            ).where(ReferencePoint.id.in_(rp_ids))
        )
    ).all()

    rp_material_map: dict[uuid.UUID, str | None] = {
        row.id: row.target_order_material for row in rp_rows
    }
    rp_level_map: dict[uuid.UUID, str] = {
        row.id: row.target_level for row in rp_rows
    }

    # Collect unique material codes to batch-load production orders
    materials = [m for m in rp_material_map.values() if m]
    po_rows = (
        await db.execute(
            select(
                ProductionOrder.id,
                ProductionOrder.material_code,
                ProductionOrder.level,
            ).where(ProductionOrder.material_code.in_(materials))
        )
    ).all()

    material_to_po: dict[str, tuple[uuid.UUID, ProductionOrderLevel]] = {
        row.material_code: (row.id, row.level) for row in po_rows
    }

    topo_order = list(nx.topological_sort(dag))
    nodes: list[SchedulingNode] = []
    for rank, rp_id in enumerate(topo_order):
        mat = rp_material_map.get(rp_id)
        if mat and mat in material_to_po:
            po_id, po_level = material_to_po[mat]
        else:
            # Reference point exists in the DAG but has no matching production order
            # (can happen with partial data). Skip gracefully.
            continue
        nodes.append(
            SchedulingNode(
                rp_id=rp_id,
                production_order_id=po_id,
                level=po_level,
                priority_rank=rank,
            )
        )
    return nodes


def get_roots(dag: nx.DiGraph) -> list[uuid.UUID]:
    """Return DAG nodes with no predecessors (in-degree == 0).

    These are the reference points with highest scheduling priority.
    """
    return [node for node, in_deg in dag.in_degree() if in_deg == 0]


async def resolve_blocking_orders(
    rp_id: uuid.UUID,
    db: AsyncSession,
) -> list[uuid.UUID]:
    """Return all production_order_ids that must be COMPLETED before
    the machine-level operation associated with *rp_id* may start.

    The blocking order is the one whose material_code matches
    reference_point.target_order_material.
    """
    from app.models.production import ProductionOrder
    from app.models.reference import ReferencePoint

    rp = (
        await db.execute(
            select(ReferencePoint).where(ReferencePoint.id == rp_id)
        )
    ).scalar_one_or_none()

    if rp is None or not rp.target_order_material:
        return []

    rows = (
        await db.execute(
            select(ProductionOrder.id).where(
                ProductionOrder.material_code == rp.target_order_material
            )
        )
    ).all()

    return [row.id for row in rows]
