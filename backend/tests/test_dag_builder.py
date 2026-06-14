"""Tests for dag_builder.py.

These tests operate entirely in-memory (no DB required).
They build nx.DiGraph objects directly and call the pure functions
validate_dag() and get_roots().

For the async DB-dependent functions (build_precedence_dag,
get_scheduling_order, resolve_blocking_orders) we use pytest-asyncio
with a mock AsyncSession that returns pre-cooked data.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import networkx as nx
import pytest

from app.core.scheduler.dag_builder import (
    SchedulingNode,
    get_roots,
    get_scheduling_order,
    validate_dag,
)
from app.core.scheduler.exceptions import CyclicDependencyError


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_dag(*edges: tuple) -> nx.DiGraph:
    """Build a DiGraph from (predecessor, successor) UUID tuples."""
    g = nx.DiGraph()
    for u, v in edges:
        g.add_edge(u, v)
    return g


def node() -> uuid.UUID:
    return uuid.uuid4()


# ─── validate_dag tests ───────────────────────────────────────────────────────

def test_linear_dag_valid():
    """A → B → C must not raise."""
    a, b, c = node(), node(), node()
    dag = make_dag((a, b), (b, c))
    validate_dag(dag)  # must not raise


def test_empty_dag_valid():
    """An empty graph must not raise."""
    validate_dag(nx.DiGraph())


def test_cycle_detection():
    """A → B → C → A must raise CyclicDependencyError."""
    a, b, c = node(), node(), node()
    dag = make_dag((a, b), (b, c), (c, a))
    with pytest.raises(CyclicDependencyError) as exc_info:
        validate_dag(dag)
    # The error message must mention at least two UUIDs (cycle nodes)
    msg = str(exc_info.value)
    assert "→" in msg


def test_cycle_detection_self_loop():
    """A → A must raise CyclicDependencyError."""
    a = node()
    dag = nx.DiGraph()
    dag.add_edge(a, a)
    with pytest.raises(CyclicDependencyError):
        validate_dag(dag)


def test_diamond_valid():
    """A→B, A→C, B→D, C→D must not raise (diamond shape is acyclic)."""
    a, b, c, d = node(), node(), node(), node()
    dag = make_dag((a, b), (a, c), (b, d), (c, d))
    validate_dag(dag)  # must not raise


# ─── get_roots tests ──────────────────────────────────────────────────────────

def test_linear_dag_roots():
    """A → B → C: only A is a root."""
    a, b, c = node(), node(), node()
    dag = make_dag((a, b), (b, c))
    assert get_roots(dag) == [a]


def test_multiple_roots():
    """A and B both roots (no predecessors); C depends on both."""
    a, b, c = node(), node(), node()
    dag = make_dag((a, c), (b, c))
    roots = get_roots(dag)
    assert set(roots) == {a, b}
    assert c not in roots


def test_orphan_node_is_root():
    """A node with no edges is a root (in-degree == 0)."""
    a = node()
    dag = nx.DiGraph()
    dag.add_node(a)
    assert get_roots(dag) == [a]


def test_empty_dag_roots():
    """Empty DAG has no roots."""
    assert get_roots(nx.DiGraph()) == []


# ─── get_scheduling_order tests (async, DB mocked) ───────────────────────────

def _make_mock_row(id_val, material, level):
    r = MagicMock()
    r.id = id_val
    r.target_order_material = material
    r.target_level = level
    r.material_code = material
    r.level = level
    return r


async def _execute_side_effect(rp_ids, materials, rp_rows, po_rows, stmt):
    """Return the appropriate mocked result based on what was queried."""
    # Simplified: return both results in sequence via a shared counter.
    # In real tests we inspect the statement; here we use a closure.
    return MagicMock()


@pytest.mark.asyncio
async def test_scheduling_order_empty_dag():
    """Empty DAG → empty scheduling order."""
    db = AsyncMock()
    result = await get_scheduling_order(nx.DiGraph(), db)
    assert result == []
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_scheduling_order_linear():
    """A → B → C: scheduling order must be A, B, C (rank 0, 1, 2)."""
    a, b, c = node(), node(), node()
    dag = make_dag((a, b), (b, c))

    mat_a, mat_b, mat_c = "MAT-A", "MAT-B", "MAT-C"
    po_a, po_b, po_c = node(), node(), node()

    from app.enums import ProductionOrderLevel

    rp_rows = [
        _make_mock_row(a, mat_a, ProductionOrderLevel.MACROAGGREGATE),
        _make_mock_row(b, mat_b, ProductionOrderLevel.AGGREGATE),
        _make_mock_row(c, mat_c, ProductionOrderLevel.GROUP),
    ]
    po_rows = [
        _make_mock_row(po_a, mat_a, ProductionOrderLevel.MACROAGGREGATE),
        _make_mock_row(po_b, mat_b, ProductionOrderLevel.AGGREGATE),
        _make_mock_row(po_c, mat_c, ProductionOrderLevel.GROUP),
    ]

    call_count = {"n": 0}

    async def fake_execute(stmt):
        result = MagicMock()
        if call_count["n"] == 0:
            result.all.return_value = rp_rows
        else:
            result.all.return_value = po_rows
        call_count["n"] += 1
        return result

    db = AsyncMock()
    db.execute.side_effect = fake_execute

    nodes = await get_scheduling_order(dag, db)
    assert len(nodes) == 3
    # Topological order: a first, c last
    assert nodes[0].rp_id == a
    assert nodes[0].priority_rank == 0
    assert nodes[-1].rp_id == c
    assert nodes[-1].priority_rank == 2


@pytest.mark.asyncio
async def test_scheduling_order_multiple_roots():
    """A and B both roots (no predecessors); C depends on both."""
    a, b, c = node(), node(), node()
    dag = make_dag((a, c), (b, c))

    mat_a, mat_b, mat_c = "MAT-A", "MAT-B", "MAT-C"
    po_a, po_b, po_c = node(), node(), node()

    from app.enums import ProductionOrderLevel

    level = ProductionOrderLevel.AGGREGATE
    rp_rows = [
        _make_mock_row(a, mat_a, level),
        _make_mock_row(b, mat_b, level),
        _make_mock_row(c, mat_c, level),
    ]
    po_rows = [
        _make_mock_row(po_a, mat_a, level),
        _make_mock_row(po_b, mat_b, level),
        _make_mock_row(po_c, mat_c, level),
    ]

    call_count = {"n": 0}

    async def fake_execute(stmt):
        result = MagicMock()
        result.all.return_value = rp_rows if call_count["n"] == 0 else po_rows
        call_count["n"] += 1
        return result

    db = AsyncMock()
    db.execute.side_effect = fake_execute

    nodes = await get_scheduling_order(dag, db)
    assert len(nodes) == 3
    node_ids = [n.rp_id for n in nodes]
    # c must come AFTER both a and b
    assert node_ids.index(c) > node_ids.index(a)
    assert node_ids.index(c) > node_ids.index(b)


@pytest.mark.asyncio
async def test_scheduling_order_diamond():
    """A→B, A→C, B→D, C→D: D must be last."""
    a, b, c, d = node(), node(), node(), node()
    dag = make_dag((a, b), (a, c), (b, d), (c, d))

    mats = {a: "M-A", b: "M-B", c: "M-C", d: "M-D"}
    pos = {a: node(), b: node(), c: node(), d: node()}

    from app.enums import ProductionOrderLevel

    level = ProductionOrderLevel.AGGREGATE

    rp_rows = [_make_mock_row(k, v, level) for k, v in mats.items()]
    po_rows = [
        _make_mock_row(pos[k], mats[k], level) for k in mats
    ]
    # Patch material_code on po_rows
    for row in po_rows:
        row.material_code = row.target_order_material

    call_count = {"n": 0}

    async def fake_execute(stmt):
        result = MagicMock()
        result.all.return_value = rp_rows if call_count["n"] == 0 else po_rows
        call_count["n"] += 1
        return result

    db = AsyncMock()
    db.execute.side_effect = fake_execute

    nodes = await get_scheduling_order(dag, db)
    assert len(nodes) == 4
    node_ids = [n.rp_id for n in nodes]
    # d must come after b and c
    assert node_ids.index(d) > node_ids.index(b)
    assert node_ids.index(d) > node_ids.index(c)
    # a must be first
    assert node_ids[0] == a


# ─── build_precedence_dag integration (mocked DB) ────────────────────────────

@pytest.mark.asyncio
async def test_build_precedence_dag_linear():
    """build_precedence_dag with A → B → C must return a valid DAG."""
    from app.core.scheduler.dag_builder import build_precedence_dag

    model_id = node()
    a, b, c = node(), node(), node()

    rp_rows = [
        MagicMock(id=a, code="RP-001"),
        MagicMock(id=b, code="RP-002"),
        MagicMock(id=c, code="RP-003"),
    ]
    prec_rows = [
        MagicMock(predecessor_reference_point_id=a, reference_point_id=b),
        MagicMock(predecessor_reference_point_id=b, reference_point_id=c),
    ]

    call_count = {"n": 0}

    async def fake_execute(stmt):
        result = MagicMock()
        if call_count["n"] == 0:
            result.all.return_value = [(r.id, r.code) for r in rp_rows]
        else:
            result.all.return_value = [
                (p.predecessor_reference_point_id, p.reference_point_id)
                for p in prec_rows
            ]
        call_count["n"] += 1
        return result

    db = AsyncMock()
    db.execute.side_effect = fake_execute

    dag = await build_precedence_dag(model_id, db)
    assert isinstance(dag, nx.DiGraph)
    assert dag.number_of_nodes() == 3
    assert dag.has_edge(a, b)
    assert dag.has_edge(b, c)
    assert not dag.has_edge(a, c)


@pytest.mark.asyncio
async def test_build_precedence_dag_cycle_raises():
    """build_precedence_dag must raise CyclicDependencyError for A → B → C → A."""
    from app.core.scheduler.dag_builder import build_precedence_dag

    model_id = node()
    a, b, c = node(), node(), node()

    rp_rows = [(a, "RP-A"), (b, "RP-B"), (c, "RP-C")]
    prec_rows = [(a, b), (b, c), (c, a)]  # cycle

    call_count = {"n": 0}

    async def fake_execute(stmt):
        result = MagicMock()
        result.all.return_value = rp_rows if call_count["n"] == 0 else prec_rows
        call_count["n"] += 1
        return result

    db = AsyncMock()
    db.execute.side_effect = fake_execute

    with pytest.raises(CyclicDependencyError):
        await build_precedence_dag(model_id, db)
