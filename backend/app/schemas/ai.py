"""Pydantic v2 schemas for AiSuggestion and AiChatSession."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.enums import AiSuggestionType


# ── AiSuggestion ──────────────────────────────────────────────────────────────

class AiSuggestionBase(BaseModel):
    scenario_id: uuid.UUID | None = None
    machine_order_id: uuid.UUID
    suggestion_type: AiSuggestionType
    suggestion_text: str | None = None
    suggested_actions_json: list | None = None
    confidence_score: float | None = None
    accepted: bool | None = None


class AiSuggestionCreate(AiSuggestionBase):
    pass


class AiSuggestionRead(AiSuggestionBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime


# ── AiChatSession ─────────────────────────────────────────────────────────────

class AiChatSessionBase(BaseModel):
    scenario_id: uuid.UUID | None = None
    machine_order_id: uuid.UUID
    messages_json: list | None = None
    last_activity: datetime | None = None


class AiChatSessionCreate(AiChatSessionBase):
    pass


class AiChatSessionRead(AiChatSessionBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime


# ── Chat request / response ───────────────────────────────────────────────────

class ChatRequest(BaseModel):
    machine_order_id: uuid.UUID
    scenario_id: uuid.UUID | None = None
    message: str
    session_id: uuid.UUID | None = None


class ChatResponse(BaseModel):
    session_id: uuid.UUID
    message: str
    action_type: str = "INFO"
    data: dict | None = None
    apply_actions: list | None = None


# ── Delay AI response ─────────────────────────────────────────────────────────

class DelayImpactAiResponse(BaseModel):
    summary: str
    impacted_operations: list[str]
    estimated_delta_days: float
    mitigation_actions: list[str]


# ── Compare scenarios AI result ───────────────────────────────────────────────

class ScenarioCompareAiResult(BaseModel):
    recommendation: str
    delta_summary: str
    preferred_scenario: str
    reasons: list[str]
