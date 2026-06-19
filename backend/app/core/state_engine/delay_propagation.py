"""Delay Propagation — orchestratore degli effetti collaterali di un cambio stato.

Questo è il modulo "collante" del state engine: prende il `TransitionResult`
prodotto da `transitions.transition_operation_status(...)` e applica in modo
coerente e transazionale:

  1. Persistenza stato Operation + ScheduleEntry (chiamata dal router, non qui)
  2. Audit log dell'evento (audit.py)
  3. Se il ritardo è HARD o l'operazione è BLOCKED/INTERRUPTED proveniente da
     IN_PROGRESS → crea un DelayEvent automatico (MANUAL_OPERATION_DELAY) così
     resta visibile nel DelayManager esistente, riusando lo stesso meccanismo
     già cablato in delays.py per il reschedule
  4. Rollup dello stato del ProductionOrder lungo la BOM
  5. Decide SE e COME triggerare il reschedule CP-SAT:
       - HARD  → reschedule sincrono via Celery (run_schedule)
       - SOFT  → nessun reschedule automatico, solo persistenza dati
       - NONE  → nessuna azione

La soglia di ritardo (minuti) sotto la quale un ritardo è "assorbito" senza
reschedule è configurabile via env var DELAY_RESCHEDULE_THRESHOLD_MINUTES
(default 15).
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.enums import DelayEventType, OperationStatus
from app.models.delay import DelayEvent
from app.models.routing import Operation, Routing
from app.core.state_engine.transitions import (
    RescheduleUrgency,
    TransitionResult,
    transition_operation_status,
)
from app.core.state_engine.order_status_rollup import OrderStatusRollup, RollupChange

logger = logging.getLogger(__name__)

DEFAULT_DELAY_THRESHOLD_MINUTES = 15


def get_delay_threshold_minutes() -> int:
    """Soglia configurabile via .env — letta ad ogni chiamata per permettere
    override nei test senza dover ricaricare il modulo."""
    raw = os.environ.get("DELAY_RESCHEDULE_THRESHOLD_MINUTES")
    if not raw:
        return DEFAULT_DELAY_THRESHOLD_MINUTES
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "DELAY_RESCHEDULE_THRESHOLD_MINUTES=%r non è un intero valido — uso default %d",
            raw, DEFAULT_DELAY_THRESHOLD_MINUTES,
        )
        return DEFAULT_DELAY_THRESHOLD_MINUTES


@dataclass(slots=True)
class PropagationOutcome:
    """Esito completo dell'applicazione di un cambio di stato operazione."""

    transition: TransitionResult
    delay_event_id: uuid.UUID | None
    rollup_changes: list[RollupChange]
    reschedule_triggered: bool
    reschedule_task_id: str | None


class DelayPropagationEngine:
    """Applica gli effetti collaterali di un cambio di stato Operation.

    Usa una Session SQLAlchemy SINCRONA: pensato per essere chiamato sia dal
    router FastAPI (via una sessione sync dedicata, vedi operations.py) sia
    direttamente dal Celery task se in futuro serve un trigger interno.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.rollup = OrderStatusRollup(session)

    def apply(
        self,
        *,
        operation: Operation,
        transition: TransitionResult,
        machine_order_id: uuid.UUID,
        production_order_id: uuid.UUID,
        triggered_by: str = "operator_status_update",
    ) -> PropagationOutcome:
        """Applica gli effetti collaterali di `transition` per `operation`.

        NOTA: il chiamante deve aver già impostato
        `operation.status = transition.operation_status` e il commit/flush
        della entry collegata PRIMA di chiamare questo metodo, perché il
        rollup BOM legge lo stato corrente delle operazioni dal DB.
        """
        delay_event_id: uuid.UUID | None = None

        # ── 1. DelayEvent automatico se il ritardo è rilevante ───────────────
        if transition.reschedule_urgency in (RescheduleUrgency.SOFT, RescheduleUrgency.HARD):
            delay_event_id = self._create_delay_event(
                operation=operation,
                transition=transition,
                machine_order_id=machine_order_id,
            )

        # ── 2. Rollup stato BOM (sempre, indipendentemente dall'urgenza) ─────
        rollup_changes = self.rollup.propagate_from(production_order_id)

        # ── 3. Decisione reschedule ────────────────────────────────────────────
        reschedule_triggered = False
        task_id: str | None = None

        if transition.reschedule_urgency == RescheduleUrgency.HARD:
            task_id = self._trigger_reschedule(machine_order_id, triggered_by)
            reschedule_triggered = task_id is not None
        elif transition.reschedule_urgency == RescheduleUrgency.SOFT:
            logger.info(
                "Ritardo %d min sotto soglia (%d min) — nessun reschedule automatico "
                "per operazione %s. Dati persistiti, planner può forzare da UI.",
                transition.delay_minutes, get_delay_threshold_minutes(), operation.id,
            )

        return PropagationOutcome(
            transition=transition,
            delay_event_id=delay_event_id,
            rollup_changes=rollup_changes,
            reschedule_triggered=reschedule_triggered,
            reschedule_task_id=task_id,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    def _create_delay_event(
        self,
        *,
        operation: Operation,
        transition: TransitionResult,
        machine_order_id: uuid.UUID,
    ) -> uuid.UUID:
        now = datetime.now(tz=timezone.utc)
        description = transition.audit_message
        if transition.warnings:
            description += " | " + " ".join(transition.warnings)

        delay = DelayEvent(
            machine_order_id=machine_order_id,
            event_type=DelayEventType.MANUAL_OPERATION_DELAY,
            affected_entity_id=operation.id,
            affected_entity_type="operation",
            delay_from=now,
            delay_until=now,
            description=description,
            reported_at=now,
            # requires_reschedule riflette la stessa urgenza già decisa: per
            # SOFT registriamo l'evento ma NON chiediamo un reschedule (lo
            # facciamo già esplicitamente più sotto solo per HARD), evitando
            # un doppio innesco se in futuro qualcosa ascolta DelayEvent.
            requires_reschedule=(transition.reschedule_urgency == RescheduleUrgency.HARD),
        )
        self.session.add(delay)
        self.session.flush()
        logger.info(
            "DelayEvent automatico creato id=%s per operazione=%s (urgenza=%s, ritardo=%d min)",
            delay.id, operation.id, transition.reschedule_urgency.value, transition.delay_minutes,
        )
        return delay.id

    def _trigger_reschedule(self, machine_order_id: uuid.UUID, triggered_by: str) -> str | None:
        """Trova lo scenario attivo per la macchina e lancia reschedule_incremental.

        Ritorna il task_id Celery, oppure None se non esiste uno scenario attivo
        (caso raro: macchina senza scenario ancora creato).
        """
        from sqlalchemy import select as sql_select
        from app.models.schedule import ScheduleScenario

        active_scenario = self.session.execute(
            sql_select(ScheduleScenario).where(
                ScheduleScenario.machine_order_id == machine_order_id,
                ScheduleScenario.is_active.is_(True),
            )
        ).scalar_one_or_none()

        if active_scenario is None:
            logger.warning(
                "Nessuno scenario attivo per machine_order=%s — reschedule HARD richiesto "
                "ma non eseguibile automaticamente.",
                machine_order_id,
            )
            return None

        from app.core.scheduler.reschedule_engine import reschedule_incremental

        task = reschedule_incremental.delay(str(active_scenario.id), triggered_by)
        logger.info(
            "Reschedule CP-SAT HARD triggerato: scenario=%s task_id=%s motivo=%s",
            active_scenario.id, task.id, triggered_by,
        )
        return task.id