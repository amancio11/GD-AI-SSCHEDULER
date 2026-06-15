"""MES Production Scheduler — FastAPI application entry point."""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logging.basicConfig(
    level=logging.INFO if os.getenv("ENVIRONMENT") != "development" else logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="MES Production Scheduler",
    description="Intelligent production scheduler for complex industrial machine assembly.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Narrow this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── WebSocket connection manager ──────────────────────────────────────────────
from app.websocket.manager import manager  # noqa: E402


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness probe — risponde {"status": "ok"} se il processo è vivo."""
    return {"status": "ok"}


# ── WebSocket endpoint ────────────────────────────────────────────────────────
@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str) -> None:
    """WebSocket endpoint; clients join a room (scenario_id or machine_order_id)."""
    await manager.connect(websocket, room_id)
    try:
        while True:
            # Keep the connection alive; actual messages are pushed by the server.
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket, room_id)


# ── API routers ───────────────────────────────────────────────────────────────
# Ogni router è registrato con il prefisso /api in modo che il frontend
# (configurato su VITE_API_URL) possa chiamare /api/<resource>.
# I tag corrispondono alle sezioni della documentazione OpenAPI (/docs).
from app.api.routes import orders, operators, reference_points, delays, missing_components
from app.api.routes import export, ai, admin
from app.api.routes.schedule import router as scenarios_router, schedule_router
from app.api.routes.dag import router as dag_router

API_PREFIX = "/api"

# Ordini macchina, BOM tree, ProductionOrders, Operations
app.include_router(orders.router, prefix=API_PREFIX)

# Scenari di scheduling e trigger CP-SAT
app.include_router(scenarios_router, prefix=API_PREFIX)

# Entries del piano (lista, aggiornamento, dati Gantt)
app.include_router(schedule_router, prefix=API_PREFIX)

# Operatori, turni, calendario disponibilità
app.include_router(operators.router, prefix=API_PREFIX)

# Reference point e DAG precedenze
app.include_router(reference_points.router, prefix=API_PREFIX)

# Delay event (ritardi che impattano il piano)
app.include_router(delays.router, prefix=API_PREFIX)

# Componenti mancanti
app.include_router(missing_components.router, prefix=API_PREFIX)

# Export: CSV, JSON SAP, PDF
app.include_router(export.router, prefix=API_PREFIX)

# AI: chat, ottimizzazione, analisi ritardi, what-if, ecc.
app.include_router(ai.router, prefix=API_PREFIX)

# DB Admin: lettura e modifica diretta delle tabelle (solo per sviluppo)
app.include_router(admin.router, prefix=API_PREFIX)

app.include_router(dag_router, prefix="/api")

@app.on_event("startup")
async def on_startup() -> None:
    logger.info("MES Production Scheduler avviato — ambiente: %s", os.getenv("ENVIRONMENT", "unknown"))
