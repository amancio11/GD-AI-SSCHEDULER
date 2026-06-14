"""Reschedule Engine — Celery tasks that orchestrate the full rescheduling pipeline.

IMPORTANT: Celery workers do NOT support Python asyncio natively.
All DB access here uses a *synchronous* SQLAlchemy engine (psycopg2 / pg8000).
The async engine defined in app.db.session is used only by FastAPI request handlers.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime, timezone

from celery import shared_task
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, update
from sqlalchemy.orm import Session, sessionmaker

from celery_worker import celery_app

load_dotenv()

logger = logging.getLogger(__name__)

# ── Synchronous DB engine for Celery workers ─────────────────────────────────
_SYNC_DB_URL: str = (
    os.environ.get("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    .replace("postgresql://", "postgresql+psycopg2://")
    or "postgresql+psycopg2://scheduler:scheduler@localhost:5432/scheduler"
)

_sync_engine = create_engine(_SYNC_DB_URL, pool_pre_ping=True)
_SyncSession = sessionmaker(bind=_sync_engine, autocommit=False, autoflush=False)


def _get_sync_session() -> Session:
    return _SyncSession()


# ── Celery task stubs for AI (now delegates to proactive_analyzer) ───────────

@celery_app.task(name="app.core.scheduler.reschedule_engine.analyze_proactive")
def analyze_proactive(scenario_id: str) -> None:
    """Delegate to the real proactive analyzer (Step 18 implementation)."""
    from app.core.ai.proactive_analyzer import analyze_proactive_after_schedule  # noqa: PLC0415
    analyze_proactive_after_schedule.delay(scenario_id)


# ── Main rescheduling task ────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.core.scheduler.reschedule_engine.reschedule_incremental",
    max_retries=3,
    default_retry_delay=5,
)
def reschedule_incremental(self, scenario_id: str, triggered_by: str = "manual") -> dict:
    """Incrementally reschedule non-COMPLETED operations for *scenario_id*.

    Idempotent: calling this twice for the same scenario produces the same
    result because STALE entries are cleaned up at the end.

    Steps
    -----
    1.  Load scenario and all non-COMPLETED operations (sync DB session).
    2.  Mark all existing schedule_entries for this scenario as STALE.
    3.  Identify IN_PROGRESS operations → use their end time as a lower bound.
    4.  Load missing components, reference-point precedences, operators.
    5.  Pre-process operator shifts (shift_preprocessor).
    6.  Build reference-point precedence DAG (dag_builder — sync wrapper).
    7.  Solve CP-SAT (cpsat_model_builder).
    8.  Persist new schedule_entries.
    9.  Delete STALE entries.
    10. Broadcast RESCHEDULE_COMPLETE via WebSocket (best-effort).
    11. Trigger proactive AI analysis task.
    """
    logger.info(
        "reschedule_incremental START scenario=%s triggered_by=%s",
        scenario_id, triggered_by,
    )

    session = _get_sync_session()
    try:
        result = _run_reschedule(session, uuid.UUID(scenario_id), triggered_by)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("reschedule_incremental FAILED scenario=%s", scenario_id)
        raise self.retry(exc=exc)
    finally:
        session.close()

    # Step 10 — Broadcast (best-effort, non-blocking via asyncio.run in thread)
    _broadcast_complete(scenario_id, result)

    # Step 11 — Trigger proactive AI analysis
    analyze_proactive.delay(scenario_id)

    logger.info("reschedule_incremental DONE scenario=%s result=%s", scenario_id, result)
    return result


def _run_reschedule(session: Session, scenario_id: uuid.UUID, triggered_by: str) -> dict:
    """Core rescheduling logic (synchronous).

    Returns a summary dict with status and makespan info.
    """
    # Import here to avoid circular imports at module level
    from app.enums import OperationStatus, ScheduleEntryStatus
    from app.models.missing import MissingComponent
    from app.models.operator import Operator, OperatorCalendar
    from app.models.production import ProductionOrder
    from app.models.reference import ReferencePoint, ReferencePointPrecedence
    from app.models.routing import Operation, Routing
    from app.models.schedule import ScheduleEntry, ScheduleScenario

    # ── Step 1: Load scenario ─────────────────────────────────────────────────
    scenario: ScheduleScenario | None = session.get(ScheduleScenario, scenario_id)
    if scenario is None:
        raise ValueError(f"Scenario {scenario_id} not found")

    machine_order_id = scenario.machine_order_id

    # ── Step 2: Mark existing entries STALE ───────────────────────────────────
    session.execute(
        update(ScheduleEntry)
        .where(ScheduleEntry.scenario_id == scenario_id)
        .values(status=ScheduleEntryStatus.STALE)
    )

    # ── Step 3: Identify IN_PROGRESS operations (keep their end time) ─────────
    in_progress_entries = (
        session.query(ScheduleEntry)
        .filter(
            ScheduleEntry.scenario_id == scenario_id,
            ScheduleEntry.status == ScheduleEntryStatus.STALE,
            # actual_start set = was in progress before marking STALE
        )
        .all()
    )
    blocking_from_in_progress: dict[uuid.UUID, int] = {}  # op_id → earliest_start_min
    # (Not blocking anything in the stub; full logic implemented once models stabilise)

    # ── Step 4: Load schedulable operations ──────────────────────────────────
    ops_rows = (
        session.query(Operation, Routing, ProductionOrder)
        .join(Routing, Operation.routing_id == Routing.id)
        .join(ProductionOrder, Routing.production_order_id == ProductionOrder.id)
        .filter(
            ProductionOrder.machine_order_id == machine_order_id,
            Operation.status.notin_([OperationStatus.COMPLETED]),
        )
        .all()
    )

    from app.core.scheduler.cpsat_types import QualifiedOperator, SchedulableOperation
    from app.enums import OperationType

    schedulable_ops: list[SchedulableOperation] = []
    for op, routing, po in ops_rows:
        # Priorità: workcenter sull'operazione → workcenter sull'ordine → skip
        wc_id = op.workcenter_id or po.workcenter_id
        if wc_id is None:
            logger.warning(
                "Operazione %s senza workcenter_id — saltata", op.id
            )
            continue
        schedulable_ops.append(
            SchedulableOperation(
                id=op.id,
                routing_id=routing.id,
                production_order_id=po.id,
                operation_type=OperationType(op.operation_type.value),
                workcenter_id=wc_id,  # ← fix
                planned_duration_minutes=op.planned_duration_minutes,
                progress_pct=op.progress_pct,
                can_be_interrupted=op.can_be_interrupted,
                earliest_start_minutes=0,
                reference_point_id=op.reference_point_id,
            )
        )


    if not schedulable_ops:
        logger.info("No schedulable operations for scenario %s — nothing to do", scenario_id)
        _cleanup_stale(session, scenario_id)
        return {"status": "SKIPPED", "reason": "no_schedulable_ops"}

    # ── Step 4b: Missing component constraints ────────────────────────────────
    from app.core.scheduler.shift_preprocessor import compute_epoch, datetime_to_minutes

    today = date.today()
    epoch = compute_epoch(today)

    missing_rows = (
        session.query(MissingComponent)
        .filter(
            MissingComponent.is_arrived.is_(False),
            MissingComponent.production_order_id.in_(
                [op.production_order_id for op in schedulable_ops]
            ),
        )
        .all()
    )

    missing_constraints: dict[uuid.UUID, int] = {}
    for mc in missing_rows:
        if mc.expected_arrival_date:
            arrival_dt = datetime(
                mc.expected_arrival_date.year,
                mc.expected_arrival_date.month,
                mc.expected_arrival_date.day,
                tzinfo=timezone.utc,
            )
            arrival_min = datetime_to_minutes(arrival_dt, epoch)
            # Apply to all ops of that production order
            for op_sc in schedulable_ops:
                if op_sc.production_order_id == mc.production_order_id:
                    missing_constraints[op_sc.id] = max(
                        missing_constraints.get(op_sc.id, 0), arrival_min
                    )

    # ── Step 4c: Operators + availability slots ────────────────────────────────
    from app.core.scheduler.shift_preprocessor import _shift_slots_for_day
    from app.enums import SkillType
    from app.models.operator import Shift

    operators_rows = session.query(Operator).filter(Operator.is_active.is_(True)).all()

    # Carica i turni in un dizionario per evitare lazy-loading nella sessione sync.
    # Senza questo, cal.shift è None e nessun operatore ha slot disponibili.
    all_shifts: dict[uuid.UUID, Shift] = {
        s.id: s for s in session.query(Shift).all()
    }

    calendar_rows = (
        session.query(OperatorCalendar)
        .filter(
            OperatorCalendar.operator_id.in_([o.id for o in operators_rows]),
            OperatorCalendar.date >= today,
        )
        .all()
    )

    # Build slots per operator
    from collections import defaultdict
    cal_by_op: dict[uuid.UUID, list] = defaultdict(list)
    for cal in calendar_rows:
        cal_by_op[cal.operator_id].append(cal)

    qualified_operators: list[QualifiedOperator] = []
    for oper in operators_rows:
        slots: list[tuple[int, int]] = []
        for cal in sorted(cal_by_op.get(oper.id, []), key=lambda c: c.date):
            if not cal.is_available or cal.shift_id is None:
                continue
            shift = all_shifts.get(cal.shift_id)
            if shift is None:
                continue
            day_slots = _shift_slots_for_day(
                day=cal.date,
                shift_start_time=shift.start_time,
                shift_end_time=shift.end_time,
                break_duration_minutes=shift.break_duration_minutes,
                epoch=epoch,
            )
            slots.extend(day_slots)
        qualified_operators.append(
            QualifiedOperator(
                id=oper.id,
                skill=SkillType(oper.skill.value),
                workcenter_id=oper.workcenter_id,
                available_slots=slots,
            )
        )

    total_slots = sum(len(q.available_slots) for q in qualified_operators)
    operators_with_slots = sum(1 for q in qualified_operators if q.available_slots)
    logger.info(
        "Operators loaded: %d total, %d with slots, %d total slot windows",
        len(qualified_operators), operators_with_slots, total_slots,
    )


    # ── DIAGNOSI WORKCENTER ───────────────────────────────────────────────────
    from collections import Counter
    wc_counts = Counter(str(op.workcenter_id) for op in schedulable_ops)
    for wc_str, count in wc_counts.items():
        opers_in_wc = [o for o in qualified_operators if str(o.workcenter_id) == wc_str]
        logger.info(
            "WC %s: %d ops, %d operatori disponibili",
            wc_str, count, len(opers_in_wc)
        )

    # ── Step 4d: Precedence pairs ─────────────────────────────────────────────
    precedence_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
    # (Operation-level pairs are derived from reference-point precedences — stub)

    # ── Step 5-7: Horizon + CP-SAT ────────────────────────────────────────────
    from app.core.scheduler.cpsat_model_builder import CpsatModelBuilder
    from app.core.scheduler.shift_preprocessor import compute_horizon_minutes

    # L'orizzonte determina il dominio delle variabili CP-SAT.
    # Troppo grande (es. 1 anno) rende il modello intrattabile.
    # Usiamo: target_finish_date se presente, altrimenti la fine del calendario
    # operatori (max 90 giorni da oggi come fallback sicuro).
    from datetime import timedelta

    if calendar_rows:
        max_cal_date = max(c.date for c in calendar_rows)
        calendar_horizon_date = max_cal_date + timedelta(days=7)
    else:
        calendar_horizon_date = date.today() + timedelta(days=90)

    if scenario.target_finish_date:
        target_date = date.fromisoformat(str(scenario.target_finish_date))
        horizon_date = min(target_date, calendar_horizon_date)
        if target_date > calendar_horizon_date:
            logger.warning(
                "Target finish date %s exceeds available operator calendar ending %s; "
                "clamping horizon to %s.",
                target_date,
                max_cal_date if calendar_rows else target_date,
                calendar_horizon_date,
            )
    else:
        horizon_date = calendar_horizon_date

    horizon = compute_horizon_minutes(horizon_date, epoch)
    logger.info("CP-SAT horizon: %s (%d minutes)", horizon_date, horizon)

    builder = CpsatModelBuilder(
        operations=schedulable_ops,
        operators=qualified_operators,
        horizon_minutes=horizon,
        epoch=epoch,
        missing_components_constraints=missing_constraints,
        precedence_pairs=precedence_pairs,
    )

    objective_mode = scenario.objective_mode.value if scenario.objective_mode else "FINISH_BY_DATE"
    params: dict = {}
    if scenario.target_finish_date:
        target_dt = datetime(
            scenario.target_finish_date.year,
            scenario.target_finish_date.month,
            scenario.target_finish_date.day,
            23, 59, tzinfo=timezone.utc,
        )
        params["target_finish_minutes"] = datetime_to_minutes(target_dt, epoch)
    
    total_work = sum(op.planned_duration_minutes for op in schedulable_ops)
    total_capacity = sum(
        (e - s) for o in qualified_operators for s, e in o.available_slots
    )
    logger.info(
        "Carico totale: %d min (%.1f giorni-persona), "
        "Capacità totale operatori: %d min (%.1f giorni-persona)",
        total_work, total_work / 480,
        total_capacity, total_capacity / 480,
    )

    # Quante ops non hanno NESSUN operatore qualificato?
    from app.core.scheduler.cpsat_types import operator_can_do
    orphan_ops = []
    for op in schedulable_ops:
        qualified = [
            o for o in qualified_operators
            if o.workcenter_id == op.workcenter_id
            and operator_can_do(o, op.operation_type)
            and o.available_slots
        ]
        if not qualified:
            orphan_ops.append((op.id, op.operation_type, op.workcenter_id))

    logger.info("Operazioni senza operatori qualificati: %d", len(orphan_ops))
    for op_id, op_type, wc_id in orphan_ops[:10]:
        logger.info("  → %s tipo=%s wc=%s", op_id, op_type, wc_id)

    solution = builder.build_and_solve(
        objective_mode=objective_mode,
        params=params,
        scenario_id=scenario_id,
    )

    # ── Step 8: Persist new entries ───────────────────────────────────────────
    if solution.status in ("OPTIMAL", "FEASIBLE"):
        for entry_schema in solution.schedule_entries:
            entry = ScheduleEntry(
                id=uuid.uuid4(),
                scenario_id=entry_schema.scenario_id,
                operation_id=entry_schema.operation_id,
                operator_id=entry_schema.operator_id,
                workcenter_id=entry_schema.workcenter_id,
                scheduled_start=entry_schema.scheduled_start,
                scheduled_end=entry_schema.scheduled_end,
                status=entry_schema.status,
                delay_minutes=0,
                is_manual_override=False,
            )
            session.add(entry)

    # ── Step 9: Delete STALE entries ──────────────────────────────────────────
    _cleanup_stale(session, scenario_id)

    makespan_days = (
        round(solution.makespan_minutes / 1440, 2)
        if solution.makespan_minutes is not None
        else None
    )

    return {
        "status": solution.status,
        "scenario_id": str(scenario_id),
        "makespan_days": makespan_days,
        "operators_used": solution.operators_used,
        "conflicts": solution.conflicts,
    }


def _cleanup_stale(session: Session, scenario_id: uuid.UUID) -> None:
    """Delete all STALE schedule entries for the scenario."""
    from app.enums import ScheduleEntryStatus
    from app.models.schedule import ScheduleEntry

    session.query(ScheduleEntry).filter(
        ScheduleEntry.scenario_id == scenario_id,
        ScheduleEntry.status == ScheduleEntryStatus.STALE,
    ).delete(synchronize_session="fetch")


def _broadcast_complete(scenario_id: str, result: dict) -> None:
    """Fire-and-forget WebSocket broadcast using asyncio.run in a thread."""
    import asyncio
    import threading

    from app.websocket.manager import manager

    async def _send() -> None:
        msg_type = (
            "RESCHEDULE_COMPLETE"
            if result.get("status") in ("OPTIMAL", "FEASIBLE")
            else "SCHEDULE_INFEASIBLE"
        )
        payload = {"type": msg_type, "scenario_id": scenario_id, **result}
        await manager.broadcast(scenario_id, payload)

    def _thread_target() -> None:
        try:
            asyncio.run(_send())
        except Exception:
            pass  # WebSocket errors must not crash the Celery task

    threading.Thread(target=_thread_target, daemon=True).start()
