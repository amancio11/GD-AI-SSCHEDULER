"""AI Layer — FastAPI router exposing all AI endpoints (Steps 17 + 19).

Step 17: optimize-schedule, analyze-delay, compare-scenarios, analyze-history,
         explain-entry, suggestions CRUD.
Step 19: chat (multi-turn), delete-chat.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ai.claude_client import ClaudeClient
from app.core.ai.context_extractor import ContextExtractor
from app.core.ai.chat_session_manager import ChatSessionManager
from app.core.ai import prompt_builder as pb
from app.core.ai import response_parser as rp
from app.db.session import get_db
from app.enums import AiSuggestionType
from app.models.ai import AiSuggestion
from app.schemas.ai import (
    AiSuggestionCreate,
    AiSuggestionRead,
    ChatRequest,
    ChatResponse,
    DelayImpactAiResponse,
    ScenarioCompareAiResult,
)

router = APIRouter(prefix="/ai", tags=["ai"])

_client    = ClaudeClient()
_extractor = ContextExtractor()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _save_suggestion(
    db: AsyncSession,
    machine_order_id: uuid.UUID,
    suggestion_type: AiSuggestionType,
    text: str,
    actions: list | None,
    scenario_id: uuid.UUID | None,
    confidence: float = 0.75,
) -> AiSuggestion:
    sugg = AiSuggestion(
        id=uuid.uuid4(),
        machine_order_id=machine_order_id,
        scenario_id=scenario_id,
        suggestion_type=suggestion_type,
        suggestion_text=text,
        suggested_actions_json=actions,
        confidence_score=confidence,
        created_at=datetime.now(timezone.utc),
    )
    db.add(sugg)
    await db.flush()
    return sugg


# ── Endpoints ─────────────────────────────────────────────────────────────────

class OptimizeRequest(AiSuggestionCreate):
    """Minimal body for optimize-schedule."""
    pass


@router.post("/optimize-schedule", response_model=AiSuggestionRead)
async def optimize_schedule(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> AiSuggestionRead:
    """Generate optimisation suggestions for the active scenario."""
    scenario_id = uuid.UUID(str(body.get("scenario_id", ""))) if body.get("scenario_id") else None
    machine_order_id = uuid.UUID(str(body["machine_order_id"]))

    ctx = await _extractor.get_schedule_context(scenario_id, db) if scenario_id else {}
    prompt = pb.build_optimize_prompt(ctx)
    raw = await _client.complete(prompt, system=pb.SYSTEM_PROMPT_BASE)

    schema = rp.parse_optimize_response(raw, machine_order_id, scenario_id)
    sugg = await _save_suggestion(
        db, machine_order_id, AiSuggestionType.ON_DEMAND,
        schema.suggestion_text or "", schema.suggested_actions_json, scenario_id,
    )
    await db.commit()
    await db.refresh(sugg)
    return AiSuggestionRead.model_validate(sugg)


@router.post("/analyze-delay", response_model=DelayImpactAiResponse)
async def analyze_delay(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> DelayImpactAiResponse:
    """Analyse the impact of a specific delay event."""
    delay_id    = uuid.UUID(str(body["delay_id"]))
    scenario_id = uuid.UUID(str(body["scenario_id"])) if body.get("scenario_id") else None
    machine_order_id = uuid.UUID(str(body["machine_order_id"]))

    delay_ctx = await _extractor.get_delay_context(delay_id, db)
    sched_ctx = await _extractor.get_schedule_context(scenario_id, db) if scenario_id else {}

    prompt = pb.build_delay_analysis_prompt(delay_ctx, sched_ctx)
    raw = await _client.complete(prompt, system=pb.SYSTEM_PROMPT_BASE)
    result = rp.parse_delay_response(raw)

    await _save_suggestion(
        db, machine_order_id, AiSuggestionType.DELAY_ANALYSIS,
        result.summary, result.mitigation_actions, scenario_id,
    )
    await db.commit()
    return result


@router.post("/compare-scenarios", response_model=ScenarioCompareAiResult)
async def compare_scenarios(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> ScenarioCompareAiResult:
    """AI-powered comparison of two scenarios."""
    scenario_a_id    = uuid.UUID(str(body["scenario_a_id"]))
    scenario_b_id    = uuid.UUID(str(body["scenario_b_id"]))
    machine_order_id = uuid.UUID(str(body["machine_order_id"]))
    objective        = str(body.get("objective", "FINISH_BY_DATE"))

    ctx_a = await _extractor.get_schedule_context(scenario_a_id, db)
    ctx_b = await _extractor.get_schedule_context(scenario_b_id, db)

    prompt = pb.build_compare_scenarios_prompt(ctx_a, ctx_b, objective)
    raw = await _client.complete(prompt, system=pb.SYSTEM_PROMPT_BASE)
    result = rp.parse_compare_response(raw)

    await _save_suggestion(
        db, machine_order_id, AiSuggestionType.WHAT_IF,
        result.recommendation, None, scenario_a_id,
    )
    await db.commit()
    return result


@router.post("/analyze-history")
async def analyze_history(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Analyse historical scheduling patterns."""
    machine_order_id = uuid.UUID(str(body["machine_order_id"]))

    # Minimal historical data: list of scenario KPIs would come from here.
    historical = {"machine_order_id": str(machine_order_id), "scenarios": []}

    prompt = pb.build_history_analysis_prompt(historical)
    raw = await _client.complete(prompt, system=pb.SYSTEM_PROMPT_BASE)

    text = raw if isinstance(raw, str) else str(raw)
    await _save_suggestion(
        db, machine_order_id, AiSuggestionType.HISTORICAL_PATTERN,
        text, None, None,
    )
    await db.commit()
    return {"analysis": raw}


@router.get("/explain-entry/{entry_id}", response_model=str)
async def explain_entry(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> str:
    """Plain-text explanation of why an entry was scheduled as-is."""
    ctx = await _extractor.get_entry_context(entry_id, db)
    if not ctx:
        raise HTTPException(status_code=404, detail="Entry not found")

    prompt = pb.build_explain_entry_prompt(ctx)
    raw = await _client.complete(prompt, system=pb.SYSTEM_PROMPT_BASE, expect_json=False)
    return rp.parse_explain_response(raw)


@router.get("/suggestions/{scenario_id}", response_model=list[AiSuggestionRead])
async def get_suggestions(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[AiSuggestionRead]:
    """List all AI suggestions for a scenario."""
    from sqlalchemy import select
    result = await db.execute(
        select(AiSuggestion)
        .where(AiSuggestion.scenario_id == scenario_id)
        .order_by(AiSuggestion.created_at.desc())
    )
    suggestions = result.scalars().all()
    return [AiSuggestionRead.model_validate(s) for s in suggestions]


@router.get("/suggestions/proactive/{machine_order_id}", response_model=list[AiSuggestionRead])
async def get_proactive_suggestions(
    machine_order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[AiSuggestionRead]:
    """List proactive suggestions for a machine order."""
    from sqlalchemy import select
    result = await db.execute(
        select(AiSuggestion)
        .where(
            AiSuggestion.machine_order_id == machine_order_id,
            AiSuggestion.suggestion_type == AiSuggestionType.PROACTIVE,
        )
        .order_by(AiSuggestion.created_at.desc())
        .limit(20)
    )
    suggestions = result.scalars().all()
    return [AiSuggestionRead.model_validate(s) for s in suggestions]


@router.patch("/suggestions/{suggestion_id}/accept", response_model=AiSuggestionRead)
async def accept_suggestion(
    suggestion_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AiSuggestionRead:
    sugg = await db.get(AiSuggestion, suggestion_id)
    if not sugg:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    sugg.accepted = True
    await db.commit()
    await db.refresh(sugg)
    return AiSuggestionRead.model_validate(sugg)


@router.patch("/suggestions/{suggestion_id}/reject", response_model=AiSuggestionRead)
async def reject_suggestion(
    suggestion_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AiSuggestionRead:
    sugg = await db.get(AiSuggestion, suggestion_id)
    if not sugg:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    sugg.accepted = False
    await db.commit()
    await db.refresh(sugg)
    return AiSuggestionRead.model_validate(sugg)


# ── Step 19 — Chat endpoints ──────────────────────────────────────────────────

_session_mgr = ChatSessionManager()


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """Multi-turn chat with Claude.

    Steps:
      1. Retrieve or create a chat session.
      2. Extract current schedule context from DB.
      3. Build system prompt (context re-injected on every call).
      4. Append the user message to the session history.
      5. Call Claude with the full history.
      6. Append Claude's reply to the session history.
      7. Parse the reply and return a ChatResponse.
    """
    session = await _session_mgr.get_or_create_session(
        machine_order_id=body.machine_order_id,
        scenario_id=body.scenario_id,
        db=db,
    )

    # Override with caller-supplied session_id if provided
    if body.session_id and body.session_id != session.id:
        supplied = await db.get(__import__("app.models.ai", fromlist=["AiChatSession"]).AiChatSession, body.session_id)
        if supplied:
            session = supplied

    # 2. Extract schedule context
    schedule_ctx = (
        await _extractor.get_schedule_context(body.scenario_id, db)
        if body.scenario_id
        else {}
    )

    # 3. System prompt with current context
    system_prompt = pb.build_chat_system_prompt(schedule_ctx)

    # 4. Append user message
    await _session_mgr.add_message(session.id, "user", body.message, db)

    # 5. Build messages list and call Claude
    messages = await _session_mgr.build_messages_for_api(session)
    reply_text = await _client.chat(messages, system=system_prompt)

    # 6. Append assistant reply
    await _session_mgr.add_message(session.id, "assistant", reply_text, db)
    await db.commit()

    # 7. Parse and return
    parsed = rp.parse_chat_response(reply_text)
    # Override session_id with the actual persisted session
    parsed.session_id = session.id
    return parsed


@router.delete("/chat/{session_id}")
async def delete_chat_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Reset (clear messages from) a chat session."""
    await _session_mgr.clear_session(session_id, db)
    await db.commit()
    return Response(status_code=204)
