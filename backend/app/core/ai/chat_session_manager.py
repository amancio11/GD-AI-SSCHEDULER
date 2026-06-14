"""Chat Session Manager — persists multi-turn conversation history in DB.

The full messages list is stored as JSON in AiChatSession.messages_json.
On every call the context is re-injected into the system prompt because
Claude has no memory between API calls.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import AiChatSession


class ChatSessionManager:
    """Manages multi-turn chat sessions backed by the ai_chat_sessions table."""

    MAX_HISTORY_MESSAGES: int = 20

    # ── Session lifecycle ─────────────────────────────────────────────────────

    async def get_or_create_session(
        self,
        machine_order_id: uuid.UUID,
        scenario_id: uuid.UUID | None,
        db: AsyncSession,
    ) -> AiChatSession:
        """Return an existing session (most recent) or create a new one.

        Priority:
          1. If *scenario_id* is given → most recent session for that scenario.
          2. Otherwise → most recent session for *machine_order_id*.
          3. If nothing found → create a new session.
        """
        stmt = (
            select(AiChatSession)
            .where(AiChatSession.machine_order_id == machine_order_id)
            .order_by(AiChatSession.last_activity.desc().nullslast())
            .limit(1)
        )
        if scenario_id:
            stmt = (
                select(AiChatSession)
                .where(
                    AiChatSession.machine_order_id == machine_order_id,
                    AiChatSession.scenario_id == scenario_id,
                )
                .order_by(AiChatSession.last_activity.desc().nullslast())
                .limit(1)
            )

        result = await db.execute(stmt)
        session = result.scalar_one_or_none()

        if session is None:
            session = AiChatSession(
                id=uuid.uuid4(),
                machine_order_id=machine_order_id,
                scenario_id=scenario_id,
                messages_json=[],
                created_at=datetime.now(timezone.utc),
                last_activity=datetime.now(timezone.utc),
            )
            db.add(session)
            await db.flush()

        return session

    async def add_message(
        self,
        session_id: uuid.UUID,
        role: str,
        content: str,
        db: AsyncSession,
    ) -> None:
        """Append a message to the session history, truncating if needed.

        If the history exceeds MAX_HISTORY_MESSAGES, the oldest messages
        are dropped (keeping the most recent ones).
        """
        session = await db.get(AiChatSession, session_id)
        if session is None:
            return

        messages: list[dict] = list(session.messages_json or [])
        messages.append({"role": role, "content": content})

        if len(messages) > self.MAX_HISTORY_MESSAGES:
            messages = messages[-self.MAX_HISTORY_MESSAGES:]

        session.messages_json = messages
        session.last_activity  = datetime.now(timezone.utc)
        await db.flush()

    async def build_messages_for_api(
        self,
        session: AiChatSession,
    ) -> list[dict]:
        """Return the message history in the format expected by the Claude API.

        Each entry is ``{"role": "user"|"assistant", "content": str}``.
        """
        messages = session.messages_json or []
        return [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]

    async def clear_session(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
    ) -> None:
        """Remove all messages from the session (keeps the session row)."""
        session = await db.get(AiChatSession, session_id)
        if session is None:
            return
        session.messages_json = []
        session.last_activity  = datetime.now(timezone.utc)
        await db.flush()
