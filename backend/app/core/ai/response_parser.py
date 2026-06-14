"""Response parsers — convert raw Claude output into typed schema objects.

Every parser has a graceful fallback so a malformed response never crashes
the request handler.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.schemas.ai import (
    AiSuggestionCreate,
    ChatResponse,
    DelayImpactAiResponse,
    ScenarioCompareAiResult,
)
from app.enums import AiSuggestionType

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _str(raw: dict, key: str, default: str = "") -> str:
    return str(raw.get(key, default))

def _float(raw: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(raw.get(key, default))
    except (TypeError, ValueError):
        return default

def _list(raw: dict, key: str) -> list:
    val = raw.get(key)
    return val if isinstance(val, list) else []


# ── Parsers ───────────────────────────────────────────────────────────────────

def parse_optimize_response(
    raw: dict,
    machine_order_id: uuid.UUID,
    scenario_id: uuid.UUID | None = None,
) -> AiSuggestionCreate:
    """Parse the response from build_optimize_prompt into an AiSuggestionCreate."""
    try:
        suggestions = _list(raw, "suggestions")
        text = _str(raw, "summary") or "\n".join(
            s.get("action", "") for s in suggestions[:3]
        )
        return AiSuggestionCreate(
            machine_order_id=machine_order_id,
            scenario_id=scenario_id,
            suggestion_type=AiSuggestionType.ON_DEMAND,
            suggestion_text=text or _str(raw, "raw"),
            suggested_actions_json=suggestions or None,
            confidence_score=0.8,
        )
    except Exception as exc:
        logger.warning("parse_optimize_response fallback: %s", exc)
        return AiSuggestionCreate(
            machine_order_id=machine_order_id,
            scenario_id=scenario_id,
            suggestion_type=AiSuggestionType.ON_DEMAND,
            suggestion_text=str(raw),
            confidence_score=0.5,
        )


def parse_delay_response(raw: dict) -> DelayImpactAiResponse:
    """Parse the response from build_delay_analysis_prompt."""
    try:
        return DelayImpactAiResponse(
            summary=_str(raw, "summary", "Nessun sommario disponibile."),
            impacted_operations=_list(raw, "impacted_operations"),
            estimated_delta_days=_float(raw, "estimated_delta_days"),
            mitigation_actions=_list(raw, "mitigation_actions"),
        )
    except Exception as exc:
        logger.warning("parse_delay_response fallback: %s", exc)
        return DelayImpactAiResponse(
            summary=str(raw),
            impacted_operations=[],
            estimated_delta_days=0.0,
            mitigation_actions=[],
        )


def parse_chat_response(raw: str | dict) -> ChatResponse:
    """Parse the response from build_chat_system_prompt.

    Claude is instructed to return JSON; if it returns plain text, wrap it.
    """
    import json

    if isinstance(raw, str):
        try:
            stripped = raw.strip()
            if stripped.startswith("```"):
                lines = stripped.split("\n")
                stripped = "\n".join(lines[1:-1])
            raw = json.loads(stripped)
        except Exception:
            # Plain-text fallback
            return ChatResponse(
                session_id=uuid.uuid4(),
                message=raw,
                action_type="INFO",
            )

    try:
        return ChatResponse(
            session_id=uuid.uuid4(),          # caller will override
            message=_str(raw, "message", str(raw)),
            action_type=_str(raw, "action_type", "INFO"),
            data=raw.get("data"),
            apply_actions=raw.get("apply_actions"),
        )
    except Exception as exc:
        logger.warning("parse_chat_response fallback: %s", exc)
        return ChatResponse(
            session_id=uuid.uuid4(),
            message=str(raw),
            action_type="INFO",
        )


def parse_compare_response(raw: dict) -> ScenarioCompareAiResult:
    """Parse the response from build_compare_scenarios_prompt."""
    try:
        return ScenarioCompareAiResult(
            recommendation=_str(raw, "recommendation", "Nessuna raccomandazione."),
            delta_summary=_str(raw, "delta_summary", ""),
            preferred_scenario=_str(raw, "preferred_scenario", "N/D"),
            reasons=_list(raw, "reasons"),
        )
    except Exception as exc:
        logger.warning("parse_compare_response fallback: %s", exc)
        return ScenarioCompareAiResult(
            recommendation=str(raw),
            delta_summary="",
            preferred_scenario="N/D",
            reasons=[],
        )


def parse_explain_response(raw: str) -> str:
    """The explain-entry response is free text — return it as-is."""
    if isinstance(raw, dict):
        return raw.get("raw", str(raw))
    return str(raw)
