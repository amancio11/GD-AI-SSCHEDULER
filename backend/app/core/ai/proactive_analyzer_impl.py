"""Implementation of proactive analysis — called from the Celery task.

Separated into its own module to keep the task file minimal and importable
without triggering the full AI stack at Celery worker startup.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 5
HIGH_UTILIZATION_THRESHOLD = 0.90
LOW_UTILIZATION_THRESHOLD  = 0.30
_MINUTES_PER_WORK_DAY      = 450   # 7.5 h


def _get_sync_session():
    """Return a synchronous SQLAlchemy session (same engine as reschedule_engine)."""
    from app.core.scheduler.reschedule_engine import _SyncSession  # noqa: PLC0415
    return _SyncSession()


# ── Rule-based detectors ──────────────────────────────────────────────────────

def _detect_overloaded_operators(
    entries: list,
    operator_names: dict[uuid.UUID, str],
) -> list[str]:
    """Return rule-based text suggestions for operators with >90 % utilisation."""
    worked: dict[uuid.UUID, int] = defaultdict(int)
    for e in entries:
        mins = int((e.scheduled_end - e.scheduled_start).total_seconds() // 60)
        worked[e.operator_id] += mins

    suggestions: list[str] = []
    for op_id, mins in worked.items():
        name = operator_names.get(op_id, str(op_id))
        pct  = mins / max(_MINUTES_PER_WORK_DAY * 5, 1)  # compare against one work-week
        if pct > HIGH_UTILIZATION_THRESHOLD:
            suggestions.append(
                f"Operatore {name} ha utilizzo {pct:.0%} questa settimana. "
                f"Considera di redistribuire alcune operazioni."
            )
    return suggestions[:MAX_SUGGESTIONS]


def _detect_missing_on_critical_path(
    missing_components: list,
    critical_path_op_ids: list[uuid.UUID],
    entries: list,
) -> list[str]:
    """Return suggestions if any missing component blocks critical-path operations."""
    critical_op_set = set(critical_path_op_ids)
    # Build: production_order_id → set of operation_ids on critical path
    po_critical: dict[uuid.UUID, bool] = {}
    for e in entries:
        if e.operation_id in critical_op_set:
            # We don't have direct po_id from entry, but can mark operation ids.
            po_critical[e.operation_id] = True

    suggestions: list[str] = []
    for mc in missing_components:
        if not mc.expected_arrival_date or mc.is_arrived:
            continue
        arrival = mc.expected_arrival_date
        delta   = (arrival - datetime.now(timezone.utc).date()).days
        suggestions.append(
            f"Il componente {mc.component_material} (arrivo {arrival}) "
            f"è sul critical path. Ritardo stimato: ~{max(0, delta)} giorni al makespan."
        )
        if len(suggestions) >= 3:
            break
    return suggestions


def _detect_target_exceeded(
    scenario,
    entries: list,
) -> str | None:
    """Return a suggestion if makespan exceeds target_finish_date."""
    if not scenario.target_finish_date or not entries:
        return None

    max_end = max(e.scheduled_end for e in entries)
    target  = datetime(
        scenario.target_finish_date.year,
        scenario.target_finish_date.month,
        scenario.target_finish_date.day,
        23, 59, tzinfo=timezone.utc,
    )
    if max_end > target:
        delta_days = (max_end - target).total_seconds() / 86400
        return (
            f"Il makespan attuale supera la data target di {delta_days:.1f} giorni. "
            f"Considera di aggiungere risorse o rinegoziare la scadenza."
        )
    return None


# ── Claude enrichment ─────────────────────────────────────────────────────────

async def _call_claude_for_mitigation(context: dict) -> list[str]:
    """Call Claude asynchronously and return a list of mitigation texts."""
    from app.core.ai.claude_client import ClaudeClient
    from app.core.ai import prompt_builder as pb

    client = ClaudeClient()
    try:
        raw = await client.complete(
            pb.build_optimize_prompt(context),
            system=pb.SYSTEM_PROMPT_BASE,
        )
        if isinstance(raw, dict):
            suggestions = raw.get("suggestions", [])
            return [s.get("action", str(s)) for s in suggestions[:3]]
        return [str(raw)[:400]]
    except Exception as exc:
        logger.warning("Claude call failed in proactive analyzer: %s", exc)
        return []


# ── Main implementation ───────────────────────────────────────────────────────

def run_proactive_analysis(scenario_id: uuid.UUID) -> dict:
    """Run the full proactive analysis and persist ai_suggestion rows.

    Returns a summary dict with the count of new suggestions.
    """
    session = _get_sync_session()
    try:
        return _analyse(session, scenario_id)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _analyse(session, scenario_id: uuid.UUID) -> dict:
    from app.enums import AiSuggestionType
    from app.models.ai import AiSuggestion
    from app.models.missing import MissingComponent
    from app.models.operator import Operator
    from app.models.schedule import ScheduleEntry, ScheduleScenario

    scenario = session.get(ScheduleScenario, scenario_id)
    if scenario is None:
        return {"new_suggestions": 0}

    entries = (
        session.query(ScheduleEntry)
        .filter(ScheduleEntry.scenario_id == scenario_id)
        .all()
    )
    if not entries:
        return {"new_suggestions": 0}

    # Operator name map
    operators = session.query(Operator).all()
    op_names: dict[uuid.UUID, str] = {o.id: o.full_name for o in operators}

    # Missing components
    missing = (
        session.query(MissingComponent)
        .filter(MissingComponent.is_arrived.is_(False))
        .all()
    )

    # Critical path (simplified — use all operations in SCHEDULED status)
    # Full critical path would require the solution_extractor; here we
    # approximate by marking all entries as candidates.
    critical_path: list[uuid.UUID] = [e.operation_id for e in entries]

    # ── Rule-based detection ──────────────────────────────────────────────────
    suggestions: list[str] = []
    is_critical = False

    overloaded = _detect_overloaded_operators(entries, op_names)
    if overloaded:
        suggestions.extend(overloaded)
        is_critical = True

    missing_path = _detect_missing_on_critical_path(missing, critical_path, entries)
    if missing_path:
        suggestions.extend(missing_path)
        is_critical = True

    target_exceeded = _detect_target_exceeded(scenario, entries)
    if target_exceeded:
        suggestions.append(target_exceeded)
        is_critical = True

    # ── Claude enrichment (only if critical issues found) ────────────────────
    claude_suggestions: list[str] = []
    if is_critical:
        context = {
            "scenario_id":   str(scenario_id),
            "scenario_name": scenario.name,
            "rule_findings": suggestions,
            "total_entries": len(entries),
        }
        try:
            claude_suggestions = asyncio.run(_call_claude_for_mitigation(context))
        except Exception as exc:
            logger.warning("asyncio.run failed in proactive: %s", exc)

    all_suggestions = suggestions + claude_suggestions
    all_suggestions = all_suggestions[:MAX_SUGGESTIONS]

    # ── Persist suggestions ───────────────────────────────────────────────────
    saved = 0
    for text in all_suggestions:
        if not text.strip():
            continue
        row = AiSuggestion(
            id=uuid.uuid4(),
            machine_order_id=scenario.machine_order_id,
            scenario_id=scenario_id,
            suggestion_type=AiSuggestionType.PROACTIVE,
            suggestion_text=text.strip(),
            suggested_actions_json=None,
            confidence_score=0.75 if text in overloaded else 0.65,
            created_at=datetime.now(timezone.utc),
        )
        session.add(row)
        saved += 1

    session.commit()

    # ── Broadcast WebSocket ───────────────────────────────────────────────────
    if saved > 0:
        _broadcast_async(str(scenario_id), saved)

    return {"new_suggestions": saved}


def _broadcast_async(scenario_id: str, count: int) -> None:
    """Fire-and-forget WebSocket broadcast from a daemon thread."""
    from app.websocket.manager import manager

    async def _send() -> None:
        await manager.broadcast(
            scenario_id,
            {"type": "AI_SUGGESTION_NEW", "count": count, "scenario_id": scenario_id},
        )

    def _thread() -> None:
        try:
            asyncio.run(_send())
        except Exception:
            pass

    threading.Thread(target=_thread, daemon=True).start()
