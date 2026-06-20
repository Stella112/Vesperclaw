"""Provider-agnostic LLM client for VesperClaw's analyst agents.

Default provider is Qwen (Alibaba) via the hackathon OpenAI-compatible endpoint.
Claude (Anthropic) is supported as a fallback. Agents call `chat_json()` to get a
parsed dict back; if the model or network fails, a deterministic fallback dict is
returned so the trading loop never crashes on an LLM hiccup.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from loguru import logger

import config


class LLMUnavailable(Exception):
    """Raised when no reasoning provider is configured/reachable."""


def _strip_json(text: str) -> str:
    """Pull the first JSON object out of a model response (handles ```json fences)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    return brace.group(0) if brace else text


class LLMClient:
    """Thin wrapper exposing chat() and chat_json() across providers."""

    def __init__(self) -> None:
        self.provider = config.LLM_PROVIDER
        self._client = None
        self._init_provider()

    def _init_provider(self) -> None:
        if self.provider == "qwen":
            if not config.QWEN_API_KEY:
                logger.warning("QWEN_API_KEY not set — LLM agents will use fallback heuristics.")
                self._client = None
                return
            from openai import OpenAI
            self._client = OpenAI(
                api_key=config.QWEN_API_KEY,
                base_url=config.QWEN_BASE_URL,
            )
            self.model = config.QWEN_MODEL
            self.fast_model = config.QWEN_FAST_MODEL
        elif self.provider == "claude":
            if not config.ANTHROPIC_API_KEY:
                logger.warning("ANTHROPIC_API_KEY not set — LLM agents will use fallback heuristics.")
                self._client = None
                return
            import anthropic
            self._client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            self.model = config.CLAUDE_MODEL
            self.fast_model = config.CLAUDE_MODEL
        else:
            raise LLMUnavailable(f"Unknown LLM_PROVIDER: {self.provider}")

    @property
    def available(self) -> bool:
        return self._client is not None

    def chat(self, system: str, user: str, fast: bool = False, max_tokens: int = 700) -> str:
        """Return the raw text completion. Retries twice on transient errors."""
        if not self.available:
            raise LLMUnavailable("No reasoning provider configured")

        model = self.fast_model if fast else self.model
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                if self.provider == "qwen":
                    resp = self._client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        temperature=0.3,
                        max_tokens=max_tokens,
                    )
                    return resp.choices[0].message.content or ""
                else:  # claude
                    resp = self._client.messages.create(
                        model=model,
                        system=system,
                        messages=[{"role": "user", "content": user}],
                        temperature=0.3,
                        max_tokens=max_tokens,
                    )
                    return resp.content[0].text
            except Exception as e:  # noqa: BLE001 - we want to retry on anything transient
                last_err = e
                logger.warning(f"LLM call failed (attempt {attempt + 1}/3): {e}")
                time.sleep(1.5 * (attempt + 1))
        raise LLMUnavailable(f"LLM call failed after retries: {last_err}")

    def chat_json(
        self,
        system: str,
        user: str,
        fallback: dict[str, Any],
        fast: bool = False,
    ) -> dict[str, Any]:
        """Return a parsed JSON dict. On any failure, return `fallback` unchanged.

        This keeps the trading loop deterministic-safe: a flaky LLM degrades the
        *explanation quality*, never the agent's ability to act.
        """
        if not self.available:
            return {**fallback, "_source": "fallback_no_llm"}
        try:
            raw = self.chat(system, user, fast=fast)
            parsed = json.loads(_strip_json(raw))
            parsed["_source"] = f"{self.provider}:{self.fast_model if fast else self.model}"
            return parsed
        except Exception as e:  # noqa: BLE001
            logger.warning(f"LLM JSON parse failed, using fallback: {e}")
            return {**fallback, "_source": "fallback_parse_error"}


# Module-level singleton so agents share one connection.
_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
