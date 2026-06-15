"""Router: Scenari di scheduling e trigger del solver CP-SAT.

Questo modulo gestisce gli scenari di pianificazione e le relative schedule entries.
Un SCENARIO è un "what-if" del piano: permette di confrontare obiettivi diversi
(es. finire entro data vs. minimizzare operatori) senza toccare il piano in produzione.

Flusso tipico:
  1. Planner crea uno scenario con POST /scenarios (obiettivo + data target)
  2. Il frontend chiama POST /scenarios/{id}/run → il solver CP-SAT gira in Celery
  3. Il frontend ascolta il WebSocket /ws/{scenario_id} per la notifica di completamento
  4. Quando completato, il planner visualizza il Gantt e confronta con altri scenari
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.schedule import ScheduleScenario, ScheduleEntry
from app.models.routing import Operation
from app.models.operator import Operator
from app.schemas.schedule import (
    ScheduleScenarioCreate,
    ScheduleScenarioUpdate,
    ScheduleScenarioRead,
    ScheduleEntryRead,
    ScheduleEntryUpdate,
    GanttEntry,
    ScenarioComparisonResult,
)

logger = logging.getLogger(__name__)

# ── Colori per il Gantt: assegnati ciclicamente per operatore ────────────────
GANTT_COLORS = [
    "#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
    "#EC4899", "#06B6D4", "#84CC16", "#F97316", "#6366F1",
]

router = APIRouter(prefix="/scenarios", tags=["scenarios"])
schedule_router = APIRouter(prefix="/schedule", tags=["schedule"])


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios CRUD
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ScheduleScenarioRead])
async def list_scenarios(
    page: int = 1,
    size: int = 50,
    db: AsyncSession = Depends(get_db),
) -> list[ScheduleScenario]:
    """Elenca tutti gli scenari di scheduling, dal più recente al meno recente.

    La paginazione è semplice (offset/limit) perché il numero di scenari per
    macchina è tipicamente piccolo (< 20). Non serve cursor-based pagination.
    """
    offset = (page - 1) * size
    result = await db.execute(
        select(ScheduleScenario)
        .order_by(ScheduleScenario.created_at.desc())
        .offset(offset)
        .limit(size)
    )
    return list(result.scalars().all())


@router.get("/{scenario_id}", response_model=ScheduleScenarioRead)
async def get_scenario(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ScheduleScenario:
    """Recupera uno scenario per ID con tutti i suoi metadati."""
    obj = await db.get(ScheduleScenario, scenario_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Scenario non trovato")
    return obj


@router.post("", response_model=ScheduleScenarioRead, status_code=201)
async def create_scenario(
    payload: ScheduleScenarioCreate,
    db: AsyncSession = Depends(get_db),
) -> ScheduleScenario:
    """Crea un nuovo scenario di scheduling.

    Logica business:
    - Se is_active=True, de-attiva gli altri scenari della stessa macchina
      (una macchina ha al più uno scenario attivo alla volta).
    - Lo scenario parte con zero entries; il solver CP-SAT le popola quando
      si chiama POST /scenarios/{id}/run.
    """
    # Se il nuovo scenario è attivo, de-attiva gli altri per la stessa macchina
    if payload.is_active:
        existing = await db.execute(
            select(ScheduleScenario).where(
                ScheduleScenario.machine_order_id == payload.machine_order_id,
                ScheduleScenario.is_active == True,  # noqa: E712
            )
        )
        for s in existing.scalars().all():
            s.is_active = False

    scenario = ScheduleScenario(**payload.model_dump())
    db.add(scenario)
    await db.commit()
    await db.refresh(scenario)
    logger.info("Creato scenario id=%s nome=%r", scenario.id, scenario.name)
    return scenario


@router.patch("/{scenario_id}", response_model=ScheduleScenarioRead)
async def update_scenario(
    scenario_id: uuid.UUID,
    payload: ScheduleScenarioUpdate,
    db: AsyncSession = Depends(get_db),
) -> ScheduleScenario:
    """Aggiorna metadati di uno scenario (es. nome, obiettivo, data target)."""
    obj = await db.get(ScheduleScenario, scenario_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Scenario non trovato")

    # Gestione attivazione: se si attiva questo scenario, de-attiva gli altri
    if payload.is_active:
        existing = await db.execute(
            select(ScheduleScenario).where(
                ScheduleScenario.machine_order_id == obj.machine_order_id,
                ScheduleScenario.is_active == True,  # noqa: E712
                ScheduleScenario.id != scenario_id,
            )
        )
        for s in existing.scalars().all():
            s.is_active = False

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)

    await db.commit()
    await db.refresh(obj)
    return obj

class ScenarioCompareRequest(BaseModel):
    scenario_a_id: uuid.UUID
    scenario_b_id: uuid.UUID


@router.post("/compare", response_model=ScenarioComparisonResult)
async def compare_scenarios_endpoint(
    payload: ScenarioCompareRequest,
    db: AsyncSession = Depends(get_db),
) -> ScenarioComparisonResult:
    """Confronta due scenari: calcola delta KPI e restituisce i Gantt."""
    sc_a = await db.get(ScheduleScenario, payload.scenario_a_id)
    sc_b = await db.get(ScheduleScenario, payload.scenario_b_id)
    if not sc_a:
        raise HTTPException(status_code=404, detail=f"Scenario A non trovato: {payload.scenario_a_id}")
    if not sc_b:
        raise HTTPException(status_code=404, detail=f"Scenario B non trovato: {payload.scenario_b_id}")

    async def _build_gantt(scenario_id: uuid.UUID) -> list[GanttEntry]:
        entries_result = await db.execute(
            select(ScheduleEntry)
            .where(ScheduleEntry.scenario_id == scenario_id)
            .order_by(ScheduleEntry.scheduled_start)
        )
        entries = list(entries_result.scalars().all())
        if not entries:
            return []
        op_ids = [e.operation_id for e in entries]
        op_result = await db.execute(select(Operation).where(Operation.id.in_(op_ids)))
        ops_map = {op.id: op for op in op_result.scalars().all()}
        operator_ids = list({e.operator_id for e in entries})
        opr_result = await db.execute(select(Operator).where(Operator.id.in_(operator_ids)))
        opr_map = {o.id: o for o in opr_result.scalars().all()}
        op_color: dict[uuid.UUID, str] = {}
        gantt: list[GanttEntry] = []
        for entry in entries:
            op = ops_map.get(entry.operation_id)
            operator = opr_map.get(entry.operator_id)
            if not op or not operator:
                continue
            if operator.id not in op_color:
                op_color[operator.id] = GANTT_COLORS[len(op_color) % len(GANTT_COLORS)]
            gantt.append(GanttEntry(
                id=entry.id,
                operation_id=op.id,
                operation_desc=op.description,
                order_id=op.routing_id,
                order_desc=None,
                operator_id=operator.id,
                operator_name=operator.full_name,
                workcenter_id=operator.workcenter_id,
                start=entry.scheduled_start,
                end=entry.scheduled_end,
                status=entry.status,
                color=op_color[operator.id],
            ))
        return gantt

    gantt_a = await _build_gantt(payload.scenario_a_id)
    gantt_b = await _build_gantt(payload.scenario_b_id)

    def _makespan(gantt: list[GanttEntry]) -> float | None:
        if not gantt:
            return None
        mn = min(e.start for e in gantt)
        mx = max(e.end for e in gantt)
        return round((mx - mn).total_seconds() / 86400, 2)

    ms_a = _makespan(gantt_a)
    ms_b = _makespan(gantt_b)
    delta_ms = round(ms_b - ms_a, 2) if ms_a is not None and ms_b is not None else None
    ops_a = len({e.operator_id for e in gantt_a})
    ops_b = len({e.operator_id for e in gantt_b})

    return ScenarioComparisonResult(
        delta_makespan_days=delta_ms,
        delta_operators=ops_b - ops_a,
        delta_utilization=None,
        gantt_a=gantt_a,
        gantt_b=gantt_b,
    )

@router.delete("/{scenario_id}")
async def delete_scenario(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Elimina uno scenario e tutte le sue entries (cascade a livello DB)."""
    obj = await db.get(ScheduleScenario, scenario_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Scenario non trovato")
    await db.delete(obj)
    await db.commit()
    return Response(status_code=204)


@router.post("/{scenario_id}/run", status_code=202)
async def run_scenario(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Avvia il solver CP-SAT per questo scenario tramite Celery.

    Il solver gira in background; quando finisce il Celery worker manda
    una notifica WebSocket al room scenario_id. Il frontend si aggiorna
    in autonomia senza polling.

    Risposta 202 Accepted: il job è stato messo in coda, non è ancora completato.
    """
    obj = await db.get(ScheduleScenario, scenario_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Scenario non trovato")

    from app.core.scheduler.scheduler_orchestrator import run_schedule
    task_id = run_schedule(scenario_id, triggered_by="api", use_celery=True)

    return {"status": "queued", "task_id": task_id, "scenario_id": str(scenario_id)}


# ─────────────────────────────────────────────────────────────────────────────
# Task status — polling per il frontend
# ─────────────────────────────────────────────────────────────────────────────

@schedule_router.get("/task/{task_id}")
async def get_task_status(task_id: str) -> dict:
    """Controlla lo stato di un task Celery.

    Usato dal frontend per il polling post-scheduling.
    Il meccanismo principale di notifica è il WebSocket, ma questo endpoint
    permette un fallback via polling ogni 2 secondi.

    Stati Celery:
      PENDING   → in coda, non ancora preso da un worker
      STARTED   → il worker ha iniziato a eseguirlo
      SUCCESS   → completato con successo
      FAILURE   → terminato con errore
    """
    import os
    from celery import Celery
    from celery.result import AsyncResult

    # Ricrea un'istanza Celery leggera solo per leggere il risultato dal backend Redis.
    # Non importiamo celery_worker per evitare import circolari nel contesto FastAPI.
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    _app = Celery(broker=redis_url, backend=redis_url)
    result = AsyncResult(task_id, app=_app)

    return {
        "task_id": task_id,
        "status": result.status,
        "ready": result.ready(),
    }



@schedule_router.get("/scenario/{scenario_id}", response_model=list[ScheduleEntryRead])
async def get_schedule_entries(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[ScheduleEntry]:
    """Restituisce tutte le entries pianificate per uno scenario.

    Ogni entry rappresenta: "l'operatore X esegue l'operazione Y dal momento A al momento B".
    È il risultato diretto dell'ottimizzazione CP-SAT.
    """
    result = await db.execute(
        select(ScheduleEntry)
        .where(ScheduleEntry.scenario_id == scenario_id)
        .order_by(ScheduleEntry.scheduled_start)
    )
    return list(result.scalars().all())


@schedule_router.patch("/entries/{entry_id}", response_model=ScheduleEntryRead)
async def update_schedule_entry(
    entry_id: uuid.UUID,
    payload: ScheduleEntryUpdate,
    db: AsyncSession = Depends(get_db),
) -> ScheduleEntry:
    """Aggiorna una singola entry (es. orario reale, stato, motivo interruzione).

    Questo endpoint è usato quando un operatore inizia, completa o interrompe
    un'attività nel piano. Lo stato INTERRUPTED + interruption_reason permette
    di tracciare le cause di stop per l'analisi AI successiva.
    """
    obj = await db.get(ScheduleEntry, entry_id)
    if not obj:
        raise HTTPException(status_code=404, detail="ScheduleEntry non trovata")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)

    # Se si imposta actual_start e status non è già IN_PROGRESS, aggiorna lo status
    if payload.actual_start and not payload.status:
        obj.status = "IN_PROGRESS"  # type: ignore[assignment]

    await db.commit()
    await db.refresh(obj)
    return obj


@schedule_router.get("/scenario/{scenario_id}/gantt-data", response_model=list[GanttEntry])
async def get_gantt_data(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[GanttEntry]:
    """Costruisce i dati arricchiti per il Gantt chart.

    Processo:
    1. Carica le schedule entries del scenario.
    2. Per ogni entry, recupera descrizione operazione, nome operatore e descrizione ordine.
    3. Aggiunge il colore in base all'operatore (per differenziarli visivamente).
    4. Il frontend (frappe-gantt) usa questi dati direttamente senza ulteriori chiamate.

    La "critical path" è marcata se lo slittamento dell'operazione ritarderebbe
    il completamento globale della macchina (heuristic: entries sul percorso più lungo).
    """
    result = await db.execute(
        select(ScheduleEntry)
        .where(ScheduleEntry.scenario_id == scenario_id)
        .order_by(ScheduleEntry.scheduled_start)
    )
    entries = list(result.scalars().all())

    # Mappa operatore → colore per la visualizzazione Gantt
    operator_color: dict[uuid.UUID, str] = {}
    color_idx = 0

    gantt_entries: list[GanttEntry] = []
    for entry in entries:
        # Assegna colore stabile per operatore (per tutta la durata della sessione)
        if entry.operator_id not in operator_color:
            operator_color[entry.operator_id] = GANTT_COLORS[color_idx % len(GANTT_COLORS)]
            color_idx += 1

        # Recupera dettagli operazione e ordine per le label del Gantt
        op = await db.get(Operation, entry.operation_id)
        operator = await db.get(Operator, entry.operator_id)

        gantt_entries.append(GanttEntry(
            id=entry.id,
            operation_id=entry.operation_id,
            operation_desc=op.description if op else None,
            order_id=op.routing.production_order_id if op and op.routing else entry.operation_id,
            order_desc=None,  # Populated from routing relationship
            operator_id=entry.operator_id,
            operator_name=operator.full_name if operator else str(entry.operator_id)[:8],
            workcenter_id=entry.workcenter_id,
            start=entry.scheduled_start,
            end=entry.scheduled_end,
            status=entry.status,
            color=operator_color[entry.operator_id],
            is_critical_path=False,  # TODO: collegare con SolutionExtractor.critical_path()
            is_manual_override=entry.is_manual_override,
        ))

    return gantt_entries
