"""Proactive Analyzer — Celery task that runs post-scheduling AI analysis.

Flow:
  1. Load schedule entries and compute per-operator / per-workcenter metrics.
  2. Apply rule-based detection (fast, no Claude call).
  3. If critical issues found, invoke Claude for mitigation suggestions.
  4. Persist up to MAX_SUGGESTIONS ai_suggestion rows.
  5. Broadcast AI_SUGGESTION_NEW via WebSocket.

Runs entirely synchronously (Celery worker thread — no asyncio).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from celery_worker import celery_app

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 5
HIGH_UTILIZATION_THRESHOLD = 0.90   # 90 %
LOW_UTILIZATION_THRESHOLD  = 0.30   # 30 %
_AVAILABLE_MINUTES_PER_DAY = 450    # 7.5 h per shift (approx)


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(
    name="app.core.ai.proactive_analyzer.analyze_proactive_after_schedule",
    max_retries=1,
    default_retry_delay=10,
    soft_time_limit=58,
    time_limit=62,
)
def analyze_proactive_after_schedule(scenario_id: str) -> dict:
    """Analyse a freshly scheduled scenario and generate proactive suggestions.

    This task is intentionally synchronous: it uses a sync DB session and
    runs Claude via asyncio.run() in a helper function.
    """
    logger.info("proactive_analyzer START scenario=%s", scenario_id)

    from app.core.ai.proactive_analyzer_impl import run_proactive_analysis

    try:
        result = run_proactive_analysis(uuid.UUID(scenario_id))
        logger.info(
            "proactive_analyzer DONE scenario=%s new_suggestions=%d",
            scenario_id, result["new_suggestions"],
        )
        return result
    except Exception as exc:
        logger.exception("proactive_analyzer FAILED scenario=%s", scenario_id)
        raise
