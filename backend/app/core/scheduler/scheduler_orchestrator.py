"""Scheduler Orchestrator — thin façade that can either:

  - Run the scheduler directly (synchronous, for tests / CLI)
  - Dispatch to the Celery task (for production use via FastAPI)

Usage (direct):
    from app.core.scheduler.scheduler_orchestrator import run_schedule
    solution = run_schedule(scenario_id, use_celery=False)

Usage (Celery):
    from app.core.scheduler.scheduler_orchestrator import run_schedule
    task_id = run_schedule(scenario_id, use_celery=True)
"""
from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


def run_schedule(
    scenario_id: uuid.UUID,
    triggered_by: str = "api",
    use_celery: bool = True,
) -> str | dict:
    """Trigger a rescheduling run for *scenario_id*.

    Args:
        scenario_id:  The scenario to reschedule.
        triggered_by: Human-readable label for audit logging.
        use_celery:   If True, dispatch to Celery and return the task ID.
                      If False, run synchronously and return the result dict.

    Returns:
        str  — Celery task ID when use_celery=True.
        dict — Result dict when use_celery=False.
    """
    if use_celery:
        from app.core.scheduler.reschedule_engine import reschedule_incremental
        result = reschedule_incremental.delay(str(scenario_id), triggered_by)
        logger.info("Dispatched reschedule task id=%s scenario=%s", result.id, scenario_id)
        return result.id
    else:
        # Direct synchronous execution — useful for tests and CLI tools.
        from app.core.scheduler.reschedule_engine import (
            _get_sync_session,
            _run_reschedule,
        )
        session = _get_sync_session()
        try:
            result = _run_reschedule(session, scenario_id, triggered_by)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
