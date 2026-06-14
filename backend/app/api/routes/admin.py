"""Router: DB Admin — lettura e modifica diretta delle tabelle del database.

Questo modulo espone endpoint REST che permettono alla pagina DBAdmin del
frontend di visualizzare e modificare i dati di qualsiasi tabella.

Attenzione: questi endpoint sono pensati SOLO per l'ambiente di sviluppo.
In produzione andrebbero protetti con autenticazione o rimossi del tutto.

Le tabelle disponibili sono quelle dell'ORM (19 tabelle del dominio MES).
Le operazioni supportate sono:
  GET  /api/admin/tables              → lista nomi tabelle
  GET  /api/admin/tables/{name}       → righe paginate (JSON)
  PUT  /api/admin/tables/{name}/{id}  → aggiorna una riga (JSON patch)
  DELETE /api/admin/tables/{name}/{id} → elimina una riga
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

router = APIRouter(prefix="/admin", tags=["admin"])

# Tabelle esposte — stessa lista dei modelli SQLAlchemy (ordine alfabetico)
ALLOWED_TABLES = [
    "ai_chat_sessions",
    "ai_suggestions",
    "delay_events",
    "machine_models",
    "machine_orders",
    "missing_components",
    "operator_calendar",
    "operators",
    "operations",
    "production_orders",
    "reference_point_precedences",
    "reference_points",
    "routings",
    "schedule_entries",
    "schedule_scenarios",
    "shifts",
    "skill_workcenter_mapping",
    "workcenters",
    "z_orders_link",
]


def _check_table(name: str) -> None:
    """Valida che il nome tabella sia nella whitelist (previene SQL injection)."""
    if name not in ALLOWED_TABLES:
        raise HTTPException(status_code=404, detail=f"Tabella '{name}' non trovata")


@router.get("/tables")
async def list_tables() -> JSONResponse:
    """Restituisce la lista delle tabelle disponibili con metadati di base."""
    return JSONResponse({"tables": ALLOWED_TABLES})


@router.get("/tables/{table_name}")
async def get_table_rows(
    table_name: str,
    page: int = 1,
    size: int = 50,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Legge le righe di una tabella con paginazione.

    Restituisce anche i nomi delle colonne per costruire la griglia nel frontend.
    Il risultato è ordinato per 'created_at' se esiste, altrimenti per 'id'.
    """
    _check_table(table_name)

    offset = (page - 1) * size

    # Conta totale righe
    count_result = await db.execute(text(f'SELECT COUNT(*) FROM "{table_name}"'))  # noqa: S608
    total = count_result.scalar_one()

    # Determina colonna di ordinamento
    cols_result = await db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t ORDER BY ordinal_position"
        ),
        {"t": table_name},
    )
    columns = [row[0] for row in cols_result.fetchall()]

    order_col = "created_at" if "created_at" in columns else "id" if "id" in columns else columns[0]

    # Legge le righe
    rows_result = await db.execute(
        text(
            f'SELECT * FROM "{table_name}" ORDER BY "{order_col}" DESC '  # noqa: S608
            f"LIMIT {size} OFFSET {offset}"
        )
    )
    rows = rows_result.fetchall()

    # Serializza: converte UUID e datetime in stringa
    def _serialize(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return v

    data = [
        {col: _serialize(val) for col, val in zip(columns, row)}
        for row in rows
    ]

    return JSONResponse({
        "table": table_name,
        "columns": columns,
        "total": total,
        "page": page,
        "size": size,
        "rows": data,
    })


@router.put("/tables/{table_name}/{row_id}")
async def update_row(
    table_name: str,
    row_id: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Aggiorna una singola riga identificata dall'id.

    Il payload è un dizionario colonna→valore. Solo le chiavi presenti nel
    payload vengono aggiornate (non serve mandare l'intera riga).
    La colonna 'id' non può essere modificata.
    """
    _check_table(table_name)

    # Rimuove 'id' dal payload per sicurezza
    payload.pop("id", None)

    if not payload:
        raise HTTPException(status_code=400, detail="Nessun campo da aggiornare")

    # Costruisce SET col1 = :col1, col2 = :col2, ...
    set_clauses = ", ".join(f'"{k}" = :{k}' for k in payload)
    params = {**payload, "_id": row_id}

    await db.execute(
        text(f'UPDATE "{table_name}" SET {set_clauses} WHERE id = :_id'),  # noqa: S608
        params,
    )
    await db.commit()

    return JSONResponse({"ok": True, "updated": row_id})


@router.delete("/tables/{table_name}/{row_id}")
async def delete_row(
    table_name: str,
    row_id: str,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Elimina una riga dalla tabella.

    Attenzione: non controlla le FK — se ci sono dipendenze il DB solleverà
    un errore di vincolo che viene restituito al frontend come 400.
    """
    _check_table(table_name)

    try:
        result = await db.execute(
            text(f'DELETE FROM "{table_name}" WHERE id = :id'),  # noqa: S608
            {"id": row_id},
        )
        await db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Riga non trovata")
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JSONResponse({"ok": True, "deleted": row_id})
