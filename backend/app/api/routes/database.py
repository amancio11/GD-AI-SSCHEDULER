# backend/app/api/routes/database.py
#
# DB Explorer — endpoint sicuri per esplorare il database.
#
# Sicurezza:
# - Whitelist tabelle (le 19 del progetto)
# - Whitelist colonne per ogni tabella (introspettate via SQLAlchemy)
# - Whitelist operatori filtro
# - JOIN tramite template predefiniti (NON SQL libero)
# - Limit massimo 1000 righe per query, default 100
# - Tutte le query usano parametri bound (no string concat)

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Table,
    and_,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.base import Base
# Import all models so that Base.metadata.tables is fully populated
import app.models  # noqa: F401

router = APIRouter(prefix="/api/database", tags=["database"])

# ============================================================================
# WHITELIST
# ============================================================================

ALLOWED_TABLES: list[str] = [
    "machine_models",
    "machine_orders",
    "production_orders",
    "z_orders_link",
    "routings",
    "operations",
    "reference_points",
    "reference_point_precedences",
    "workcenters",
    "skill_workcenter_mapping",
    "operators",
    "shifts",
    "operator_calendar",
    "missing_components",
    "schedule_scenarios",
    "schedule_entries",
    "delay_events",
    "ai_suggestions",
    "ai_chat_sessions",
]

# Operatori filtro supportati
FilterOp = Literal[
    "eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "in", "not_in", "is_null", "not_null"
]

# Template JOIN predefiniti: chiave = (left_table, right_table), valore = colonne FK
JOIN_TEMPLATES: dict[tuple[str, str], tuple[str, str]] = {
    ("schedule_entries", "operations"): ("operation_id", "id"),
    ("schedule_entries", "operators"): ("operator_id", "id"),
    ("schedule_entries", "schedule_scenarios"): ("scenario_id", "id"),
    ("operations", "routings"): ("routing_id", "id"),
    ("operations", "workcenters"): ("workcenter_id", "id"),
    ("operations", "reference_points"): ("reference_point_id", "id"),
    ("routings", "production_orders"): ("production_order_id", "id"),
    ("production_orders", "machine_orders"): ("machine_order_id", "id"),
    ("production_orders", "workcenters"): ("workcenter_id", "id"),
    ("machine_orders", "machine_models"): ("machine_model_id", "id"),
    ("machine_orders", "workcenters"): ("workcenter_id", "id"),
    ("operators", "workcenters"): ("workcenter_id", "id"),
    ("operator_calendar", "operators"): ("operator_id", "id"),
    ("operator_calendar", "shifts"): ("shift_id", "id"),
    ("missing_components", "production_orders"): ("production_order_id", "id"),
    ("reference_points", "machine_models"): ("machine_model_id", "id"),
    ("reference_point_precedences", "reference_points"): ("reference_point_id", "id"),
    ("schedule_scenarios", "machine_orders"): ("machine_order_id", "id"),
    ("ai_suggestions", "schedule_scenarios"): ("scenario_id", "id"),
    ("ai_chat_sessions", "machine_orders"): ("machine_order_id", "id"),
    ("delay_events", "machine_orders"): ("machine_order_id", "id"),
    ("z_orders_link", "production_orders"): ("child_order_id", "id"),
}

# ============================================================================
# HELPERS
# ============================================================================

def _get_table(table_name: str) -> Table:
    if table_name not in ALLOWED_TABLES:
        raise HTTPException(403, f"Tabella '{table_name}' non consentita")
    table = Base.metadata.tables.get(table_name)
    if table is None:
        raise HTTPException(404, f"Tabella '{table_name}' non trovata nello schema")
    return table


def _serialize_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, UUID):
        return str(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "value"):  # enum
        return v.value
    return v


def _apply_filter(col: Column, op: str, value: Any) -> Any:
    if op == "eq":
        return col == value
    if op == "neq":
        return col != value
    if op == "gt":
        return col > value
    if op == "gte":
        return col >= value
    if op == "lt":
        return col < value
    if op == "lte":
        return col <= value
    if op == "like":
        return col.like(value)
    if op == "ilike":
        return col.ilike(value)
    if op == "in":
        if not isinstance(value, list):
            raise HTTPException(400, "operatore 'in' richiede una lista")
        return col.in_(value)
    if op == "not_in":
        if not isinstance(value, list):
            raise HTTPException(400, "operatore 'not_in' richiede una lista")
        return col.notin_(value)
    if op == "is_null":
        return col.is_(None)
    if op == "not_null":
        return col.isnot(None)
    raise HTTPException(400, f"Operatore filtro '{op}' non supportato")


# ============================================================================
# SCHEMAS
# ============================================================================


class FilterClause(BaseModel):
    column: str
    op: FilterOp
    value: Any = None


class JoinClause(BaseModel):
    table: str  # tabella da unire alla principale


class QueryRequest(BaseModel):
    table: str
    columns: list[str] | None = None
    filters: list[FilterClause] = Field(default_factory=list)
    joins: list[JoinClause] = Field(default_factory=list)
    order_by: str | None = None
    order_dir: Literal["asc", "desc"] = "asc"
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("/tables")
async def list_tables() -> dict[str, Any]:
    """Lista tabelle disponibili con i nomi delle colonne e i loro tipi."""
    result: list[dict[str, Any]] = []
    for tname in ALLOWED_TABLES:
        table = Base.metadata.tables.get(tname)
        if table is None:
            continue
        cols: list[dict[str, Any]] = []
        for col in table.columns:
            cols.append(
                {
                    "name": col.name,
                    "type": str(col.type),
                    "nullable": col.nullable,
                    "primary_key": col.primary_key,
                    "foreign_key": [
                        f"{fk.column.table.name}.{fk.column.name}"
                        for fk in col.foreign_keys
                    ],
                }
            )
        result.append(
            {
                "name": tname,
                "columns": cols,
                "row_count_estimate": None,  # popolato sotto se richiesto
            }
        )
    return {"tables": result}


@router.get("/tables/{table_name}/count")
async def count_rows(
    table_name: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    table = _get_table(table_name)
    res = await db.execute(select(func.count()).select_from(table))
    return {"count": int(res.scalar() or 0)}


@router.get("/joins/{table_name}")
async def joinable_tables(table_name: str) -> dict[str, list[dict[str, str]]]:
    """Tabelle che possono essere unite a `table_name` tramite template predefiniti."""
    if table_name not in ALLOWED_TABLES:
        raise HTTPException(403, f"Tabella '{table_name}' non consentita")
    options: list[dict[str, str]] = []
    for (left, right), (lcol, rcol) in JOIN_TEMPLATES.items():
        if left == table_name:
            options.append({"table": right, "on": f"{table_name}.{lcol} = {right}.{rcol}"})
        elif right == table_name:
            options.append({"table": left, "on": f"{left}.{lcol} = {table_name}.{rcol}"})
    return {"joinable": options}


@router.post("/query")
async def run_query(
    body: QueryRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    table = _get_table(body.table)

    # Risolvi colonne richieste
    if body.columns:
        cols: list[Column] = []
        for cn in body.columns:
            if cn not in table.c:
                raise HTTPException(400, f"Colonna '{cn}' non trovata in {body.table}")
            cols.append(table.c[cn])
    else:
        cols = list(table.columns)

    # Costruisci la query selezionando le colonne; aggiungi prefisso per i join
    selected_labels: list[Any] = [c.label(f"{body.table}__{c.name}") for c in cols]

    joined_tables: list[Table] = []
    join_target = table
    for j in body.joins:
        if j.table not in ALLOWED_TABLES:
            raise HTTPException(403, f"Tabella join '{j.table}' non consentita")
        tpl = JOIN_TEMPLATES.get((body.table, j.table)) or JOIN_TEMPLATES.get((j.table, body.table))
        if tpl is None:
            raise HTTPException(
                400,
                f"Join {body.table} ↔ {j.table} non disponibile. Vedi /api/database/joins/{body.table}",
            )
        right = _get_table(j.table)
        # Decidi l'ordine in base a quale chiave è in body.table
        if (body.table, j.table) in JOIN_TEMPLATES:
            lcol, rcol = tpl
            on = table.c[lcol] == right.c[rcol]
        else:
            lcol, rcol = tpl
            on = right.c[lcol] == table.c[rcol]
        join_target = join_target.join(right, on)
        joined_tables.append(right)
        # Aggiungi le colonne del join al SELECT
        for c in right.columns:
            selected_labels.append(c.label(f"{j.table}__{c.name}"))

    stmt = select(*selected_labels).select_from(join_target)

    # Filtri
    conditions: list[Any] = []
    for f in body.filters:
        if "." in f.column:
            tname, colname = f.column.split(".", 1)
            tgt_table = table if tname == body.table else next(
                (t for t in joined_tables if t.name == tname), None
            )
            if tgt_table is None:
                raise HTTPException(400, f"Tabella filtro '{tname}' non in query")
            if colname not in tgt_table.c:
                raise HTTPException(400, f"Colonna filtro '{f.column}' non valida")
            conditions.append(_apply_filter(tgt_table.c[colname], f.op, f.value))
        else:
            if f.column not in table.c:
                raise HTTPException(400, f"Colonna filtro '{f.column}' non valida")
            conditions.append(_apply_filter(table.c[f.column], f.op, f.value))
    if conditions:
        stmt = stmt.where(and_(*conditions))

    # Order by
    if body.order_by:
        if "." in body.order_by:
            tname, colname = body.order_by.split(".", 1)
            tgt_table = table if tname == body.table else next(
                (t for t in joined_tables if t.name == tname), None
            )
            if tgt_table is None or colname not in tgt_table.c:
                raise HTTPException(400, f"Order by '{body.order_by}' non valido")
            order_col = tgt_table.c[colname]
        else:
            if body.order_by not in table.c:
                raise HTTPException(400, f"Order by '{body.order_by}' non valido")
            order_col = table.c[body.order_by]
        stmt = stmt.order_by(order_col.desc() if body.order_dir == "desc" else order_col.asc())

    # Count totale (senza limit/offset) — utile per paginazione
    count_stmt = select(func.count()).select_from(join_target)
    if conditions:
        count_stmt = count_stmt.where(and_(*conditions))
    total_res = await db.execute(count_stmt)
    total = int(total_res.scalar() or 0)

    stmt = stmt.limit(body.limit).offset(body.offset)
    res = await db.execute(stmt)
    rows = res.mappings().all()

    # Serializza
    serialized_rows: list[dict[str, Any]] = []
    for row in rows:
        serialized_rows.append({k: _serialize_value(v) for k, v in row.items()})

    return {
        "rows": serialized_rows,
        "total": total,
        "limit": body.limit,
        "offset": body.offset,
        "columns": list(serialized_rows[0].keys()) if serialized_rows else [
            f"{body.table}__{c.name}" for c in cols
        ],
    }
