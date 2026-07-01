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
from collections import defaultdict
from datetime import datetime, timezone
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.enums import ScheduleEntryStatus
from app.models.schedule import ScheduleScenario, ScheduleEntry
from app.models.routing import Operation, Routing
from app.core.state_engine.cpm_analyzer import CpmAnalyzer
from app.models.production import ProductionOrder
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

class CpmOperationResult(BaseModel):
    operation_id: uuid.UUID
    operation_description: str | None
    production_order_material: str
    early_start: int
    early_finish: int
    late_start: int
    late_finish: int
    total_float_minutes: int
    is_critical: bool
    delay_minutes: int
    scheduled_start: str | None
    scheduled_end: str | None
 
 
class CpmAnalysisResponse(BaseModel):
    scenario_id: uuid.UUID
    epoch: str | None
    makespan_minutes: int
    critical_path_operation_ids: list[uuid.UUID]
    operations: list[CpmOperationResult]

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

    # Calcolo utilization per ogni scenario
    def _calc_utilization(gantt: list[GanttEntry]) -> float | None:
        if not gantt:
            return None
        # Tempo totale di lavoro assegnato
        total_work_minutes = sum(
            (e.end - e.start).total_seconds() / 60
            for e in gantt
        )
        # Operatori distinti
        unique_operators = {e.operator_id for e in gantt}
        if not unique_operators:
            return None
        # Span totale dello scenario (min→max)
        mn = min(e.start for e in gantt)
        mx = max(e.end for e in gantt)
        span_minutes = (mx - mn).total_seconds() / 60
        if span_minutes <= 0:
            return None
        # Utilization = lavoro / (operatori × span)
        max_capacity = len(unique_operators) * span_minutes
        return round(total_work_minutes / max_capacity * 100, 1)
    
    util_a = _calc_utilization(gantt_a)
    util_b = _calc_utilization(gantt_b)
    delta_util = round(util_b - util_a, 1) if util_a is not None and util_b is not None else None
 
    return ScenarioComparisonResult(
        delta_makespan_days=delta_ms,
        delta_operators=ops_b - ops_a,
        delta_utilization=delta_util,
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
# Reschedule — alias semantico di /run, usato dalle sezioni che modificano i dati
# (componenti mancanti, ritardi, risorse, operazioni). Stessa pipeline incrementale
# di reschedule_incremental: ricarica mancanti/risorse/op aggiornati e riottimizza,
# preservando le IN_PROGRESS e ripulendo le entries STALE.
# ─────────────────────────────────────────────────────────────────────────────

async def _trigger_reschedule(scenario_id: uuid.UUID, db: AsyncSession, triggered_by: str) -> dict:
    obj = await db.get(ScheduleScenario, scenario_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Scenario non trovato")
    from app.core.scheduler.scheduler_orchestrator import run_schedule
    task_id = run_schedule(scenario_id, triggered_by=triggered_by, use_celery=True)
    return {"status": "queued", "task_id": task_id, "scenario_id": str(scenario_id)}


@router.post("/{scenario_id}/reschedule", status_code=202)
async def reschedule_scenario(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Rischedula incrementalmente lo scenario dopo un aggiornamento dati.

    Idempotente: ricostruisce il piano a partire dallo stato corrente del DB
    (componenti mancanti, risorse attive, durate/avanzamenti delle operazioni),
    ancorando le operazioni IN_PROGRESS a "ora". Risposta 202: gira in background,
    l'esito arriva via WebSocket `RESCHEDULE_COMPLETE` / `SCHEDULE_INFEASIBLE`.
    """
    return await _trigger_reschedule(scenario_id, db, triggered_by="reschedule-api")


@schedule_router.post("/scenario/{scenario_id}/reschedule", status_code=202)
async def reschedule_scenario_alias(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Alias di compatibilità per il path storico `/api/schedule/scenario/{id}/reschedule`."""
    return await _trigger_reschedule(scenario_id, db, triggered_by="reschedule-api")


# ─────────────────────────────────────────────────────────────────────────────
# Task status — polling per il frontend
# ─────────────────────────────────────────────────────────────────────────────

@schedule_router.get("/task/{task_id}")
async def get_task_status(task_id: str) -> dict:
    import os
    from celery import Celery
    from celery.result import AsyncResult
 
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    _app = Celery(broker=redis_url, backend=redis_url)
    result = AsyncResult(task_id, app=_app)
 
    response = {
        "task_id": task_id,
        "status": result.status,
        "ready": result.ready(),
    }
    
    # Se il task è completato, includi il risultato del solver
    if result.ready() and result.result:
        solver_result = result.result
        if isinstance(solver_result, dict):
            response["solver_status"] = solver_result.get("status")  # OPTIMAL/FEASIBLE/INFEASIBLE
            response["makespan_days"] = solver_result.get("makespan_days")
            response["operators_used"] = solver_result.get("operators_used")
            response["conflicts"] = solver_result.get("conflicts")
    
    return response


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


# ─────────────────────────────────────────────────────────────────────────────
# CPM — Critical Path Method (early/late start-finish, total float)
# ─────────────────────────────────────────────────────────────────────────────
#
# NOTA: questo endpoint vive sotto schedule_router (prefix "/schedule"), NON
# sotto router (prefix "/scenarios"), per coerenza con il path documentato in
# GUIDA_TECNICA.md sezione 9.4: GET /api/schedule/scenario/{id}/cpm.
# Nella prima integrazione era stato registrato per errore su `router`
# (→ /api/scenarios/scenario/{id}/cpm) — corretto qui.

@schedule_router.get("/scenario/{scenario_id}/cpm", response_model=CpmAnalysisResponse)
async def get_scenario_cpm_analysis(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CpmAnalysisResponse:
    """Critical Path Method per uno scenario: early/late start-finish + slack.
 
    A differenza di `is_critical_path` già presente in `gantt.py` (longest
    path semplice su `entries_payload`), questo endpoint calcola anche il
    TOTAL FLOAT di ogni operazione — quanto un'operazione può ritardare senza
    impattare il makespan finale. È la stessa metrica che strumenti come
    MS Project / Primavera P6 chiamano "Total Slack".
 
    Usa lo stesso grafo di precedenza del solver CP-SAT (Meccanismo A diretto
    + Meccanismo B espanso dai reference point), così lo slack calcolato è
    coerente con i vincoli realmente applicati nell'ultimo solve.
 
    Risponde a: GET /api/schedule/scenario/{scenario_id}/cpm
    """
    scenario = await db.get(ScheduleScenario, scenario_id)
    if not scenario:
        raise HTTPException(404, "Scenario non trovato")
 
    # ── 1. Carica le schedule_entries correnti (non STALE) con join completo ──
    entries_result = await db.execute(
        select(ScheduleEntry)
        .where(
            ScheduleEntry.scenario_id == scenario_id,
            ScheduleEntry.status != ScheduleEntryStatus.STALE,
        )
        .options(
            selectinload(ScheduleEntry.operation)
            .selectinload(Operation.routing)
            .selectinload(Routing.production_order),
            selectinload(ScheduleEntry.operation).selectinload(Operation.reference_point),
        )
    )
    entries = list(entries_result.scalars().all())
    if not entries:
        return CpmAnalysisResponse(
            scenario_id=scenario_id,
            epoch=None,
            makespan_minutes=0,
            critical_path_operation_ids=[],
            operations=[],
        )
 
    epoch = min(e.scheduled_start for e in entries)
 
    # ── 2. Durate in minuti per ogni operazione (residuo se IN_PROGRESS) ───────
    durations_minutes: dict[uuid.UUID, int] = {}
    for e in entries:
        dur = int((e.scheduled_end - e.scheduled_start).total_seconds() // 60)
        durations_minutes[e.operation_id] = dur
 
    # ── 3. Ricostruisce i vincoli di precedenza (stessa logica del solver) ────
    # 3a. Meccanismo A: precedence_pairs dirette — attualmente vuoto nel
    #     progetto (routing SIMULTANEOUS non crea precedenze interne).
    precedence_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
 
    # 3b. Meccanismo B (rp_order_constraints) + Tipo A (parent_wait): per il
    #     CPM ci basta sapere "X deve finire prima che Y inizi" a livello di
    #     singola operazione. Lo ricostruiamo dai reference_point_id presenti
    #     sulle operazioni schedulate in questo scenario, usando la stessa
    #     funzione di raccolta ricorsiva già usata in reschedule_engine.py.
    rp_order_constraints = await _build_rp_constraints_for_cpm(db, entries)
 
    # ── 4. CPM ──────────────────────────────────────────────────────────────
    cpm_results = CpmAnalyzer().analyze(
        durations_minutes=durations_minutes,
        precedence_pairs=precedence_pairs,
        rp_order_constraints=rp_order_constraints,
    )
    critical_ids = CpmAnalyzer().critical_path_ids(cpm_results)
 
    # ── 5. Serializza risposta ──────────────────────────────────────────────
    by_op_id = {e.operation_id: e for e in entries}
    operations_payload: list[CpmOperationResult] = []
    for op_id, r in cpm_results.items():
        entry = by_op_id.get(op_id)
        if entry is None:
            continue
        op = entry.operation
        po = op.routing.production_order if op.routing else None
        operations_payload.append(
            CpmOperationResult(
                operation_id=op_id,
                operation_description=op.description,
                production_order_material=po.material_code if po else "—",
                early_start=r.early_start,
                early_finish=r.early_finish,
                late_start=r.late_start,
                late_finish=r.late_finish,
                total_float_minutes=r.total_float,
                is_critical=r.is_critical,
                delay_minutes=entry.delay_minutes,
                scheduled_start=entry.scheduled_start.isoformat() if entry.scheduled_start else None,
                scheduled_end=entry.scheduled_end.isoformat() if entry.scheduled_end else None,
            )
        )
 
    makespan = max((r.early_finish for r in cpm_results.values()), default=0)
 
    return CpmAnalysisResponse(
        scenario_id=scenario_id,
        epoch=epoch.isoformat() if epoch else None,
        makespan_minutes=makespan,
        critical_path_operation_ids=critical_ids,
        operations=operations_payload,
    )
 
 
async def _build_rp_constraints_for_cpm(
    db: AsyncSession,
    entries: list[ScheduleEntry],
) -> list[tuple[list[uuid.UUID], list[uuid.UUID]]]:
    """Ricostruisce i vincoli (Tipo A: parent-wait) ai fini del CPM.
 
    Per ogni operazione con reference_point_id valorizzato, l'operazione deve
    iniziare dopo il completamento di TUTTE le operazioni schedulabili
    dell'ordine target del RP (e dei suoi figli BOM ricorsivi) — stessa
    semantica di `_collect_ops_recursive` in reschedule_engine.py.
 
    Per il CPM ci interessa solo "chi blocca chi" tra le operazioni
    EFFETTIVAMENTE presenti in questo scenario (entries correnti), quindi
    filtriamo al volo sull'insieme degli operation_id già caricati.
    """
    from app.models.reference import ReferencePoint
 
    op_ids_in_scenario = {e.operation_id for e in entries}
    ops_by_id = {e.operation_id: e.operation for e in entries}
 
    # Mappa reference_point_id → target_order_material
    rp_ids = {op.reference_point_id for op in ops_by_id.values() if op.reference_point_id}
    if not rp_ids:
        return []
 
    rp_result = await db.execute(select(ReferencePoint).where(ReferencePoint.id.in_(rp_ids)))
    rps = {rp.id: rp for rp in rp_result.scalars().all()}
 
    # Mappa material_code → production_order_id (solo tra gli ordini coinvolti)
    po_ids = {op.routing.production_order_id for op in ops_by_id.values() if op.routing}
    po_result = await db.execute(select(ProductionOrder).where(ProductionOrder.id.in_(po_ids)))
    all_relevant_orders = {po.id: po for po in po_result.scalars().all()}
 
    material_to_po_id = {po.material_code: po.id for po in all_relevant_orders.values()}
 
    # Operazioni raggruppate per production_order_id (solo quelle nello scenario)
    ops_by_order: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for op_id, op in ops_by_id.items():
        if op.routing:
            ops_by_order[op.routing.production_order_id].append(op_id)
 
    # children_map: serve per la raccolta ricorsiva. Carichiamo tutti i figli
    # diretti (parent_order_id) dei production_order coinvolti, una volta sola.
    children_result = await db.execute(
        select(ProductionOrder.id, ProductionOrder.parent_order_id)
        .where(ProductionOrder.parent_order_id.in_(po_ids))
    )
    children_map: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for child_id, parent_id in children_result.all():
        if parent_id:
            children_map[parent_id].append(child_id)
 
    def collect_ops_recursive(order_id: uuid.UUID) -> list[uuid.UUID]:
        result: list[uuid.UUID] = []
        stack = [order_id]
        visited: set[uuid.UUID] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            result.extend(ops_by_order.get(current, []))
            stack.extend(children_map.get(current, []))
        return result
 
    constraints: list[tuple[list[uuid.UUID], list[uuid.UUID]]] = []
    for op_id, op in ops_by_id.items():
        if op.reference_point_id is None:
            continue
        rp = rps.get(op.reference_point_id)
        if rp is None or not rp.target_order_material:
            continue
        target_po_id = material_to_po_id.get(rp.target_order_material)
        if target_po_id is None:
            continue
        ops_target = [o for o in collect_ops_recursive(target_po_id) if o in op_ids_in_scenario]
        if not ops_target:
            continue
        constraints.append((ops_target, [op_id]))
 
    return constraints