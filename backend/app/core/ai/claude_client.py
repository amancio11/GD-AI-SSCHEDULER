"""Async Claude API client with retry logic for 429/500 errors."""
from __future__ import annotations

import asyncio
import json
import logging
import os

import anthropic

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_STATUSES = {429, 500, 502, 503}
_TIMEOUT_SECONDS = 30.0


class ClaudeClient:
    """Thin async wrapper around the Anthropic SDK.

    Always uses ``claude-sonnet-4-6``.  Responses are returned as parsed
    JSON dicts when *expect_json* is True, otherwise as raw strings.
    """

    def __init__(self) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = "claude-sonnet-4-6"
        self.max_tokens = 2000

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_messages(self, prompt: str) -> list[dict]:
        return [{"role": "user", "content": prompt}]

    async def _call_with_retry(
        self,
        messages: list[dict],
        system: str | None,
    ) -> anthropic.types.Message:
        """Call the Messages API with exponential backoff on transient errors."""
        kwargs: dict = {
            "model":      self.model,
            "max_tokens": self.max_tokens,
            "messages":   messages,
        }
        if system:
            kwargs["system"] = system

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                loop = asyncio.get_event_loop()
                response: anthropic.types.Message = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: self.client.messages.create(**kwargs),
                    ),
                    timeout=_TIMEOUT_SECONDS,
                )
                logger.info(
                    "Claude call OK — input_tokens=%d output_tokens=%d",
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
                return response

            except anthropic.APIStatusError as exc:
                if exc.status_code in _RETRY_STATUSES:
                    wait = 2 ** attempt
                    logger.warning(
                        "Claude %d — retrying in %ds (attempt %d/%d)",
                        exc.status_code, wait, attempt + 1, _MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    last_exc = exc
                else:
                    raise

            except (asyncio.TimeoutError, anthropic.APIConnectionError) as exc:
                wait = 2 ** attempt
                logger.warning("Claude timeout/connection error — retrying in %ds", wait)
                await asyncio.sleep(wait)
                last_exc = exc

        raise RuntimeError(
            f"Claude API unreachable after {_MAX_RETRIES} retries"
        ) from last_exc

    # ── Public API ────────────────────────────────────────────────────────────

    async def complete(
        self,
        prompt: str,
        system: str | None = None,
        expect_json: bool = True,
    ) -> dict | str:
        """Send a single-turn prompt and return the response.

        Args:
            prompt:      The user message to send.
            system:      Optional system prompt override.
            expect_json: If True, parse the response text as JSON.

        Returns:
            Parsed dict if *expect_json* is True, otherwise raw text string.
        """
        messages = self._build_messages(prompt)
        response = await self._call_with_retry(messages, system)
        text = response.content[0].text

        if expect_json:
            try:
                # Claude sometimes wraps JSON in ```json … ``` fences
                stripped = text.strip()
                if stripped.startswith("```"):
                    lines = stripped.split("\n")
                    stripped = "\n".join(lines[1:-1])
                return json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning("Claude returned non-JSON — returning raw text")
                return {"raw": text}
        return text

    async def chat(
        self,
        messages: list[dict],
        system: str,
    ) -> str:
        """Multi-turn chat — accepts full message history and returns only the
        assistant text content.

        Args:
            messages: List of ``{"role": "user"|"assistant", "content": str}``.
            system:   System prompt with current schedule context.

        Returns:
            The assistant's reply as a plain string.
        """
        response = await self._call_with_retry(messages, system)
        return response.content[0].text
