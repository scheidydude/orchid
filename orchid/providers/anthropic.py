"""Anthropic Claude provider with prompt caching support."""

from __future__ import annotations

import logging
import os
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from orchid.errors import ProviderError
from orchid.providers.base import ProviderBase

logger = logging.getLogger(__name__)

_CACHE_BETA = "prompt-caching-2024-07-31"
# Minimum chars to bother caching (~512 tokens).  Below this the cache write
# cost exceeds the read savings.
_MIN_CACHE_CHARS = 512

# ── Module-level session cache stats ─────────────────────────────────────────
# Accumulated across all complete() calls in this process.  Intended to be
# read by Session.close() and reset at session start.

_session_stats: dict[str, int] = {
    "cache_writes": 0,
    "cache_hits": 0,
    "input_tokens_total": 0,
    "input_tokens_cached": 0,
}


def get_session_stats() -> dict[str, Any]:
    """Return current session cache stats with an estimated savings percentage."""
    stats = dict(_session_stats)
    total = stats["input_tokens_total"]
    cached = stats["input_tokens_cached"]
    stats["estimated_savings_pct"] = round(cached / total * 100, 1) if total > 0 else 0.0
    return stats


def reset_session_stats() -> None:
    """Reset session cache stats — call at session start."""
    _session_stats["cache_writes"] = 0
    _session_stats["cache_hits"] = 0
    _session_stats["input_tokens_total"] = 0
    _session_stats["input_tokens_cached"] = 0


# ── Provider ──────────────────────────────────────────────────────────────────


class AnthropicProvider(ProviderBase):
    """Calls the Anthropic Claude API.

    Requires ANTHROPIC_API_KEY in the environment.

    Prompt caching (D0048):
      - The system prompt is automatically wrapped with cache_control when
        caching.enabled=true and its length exceeds _MIN_CACHE_CHARS.
      - Pass cacheable_prefix=N to complete() to also cache the first N
        messages in the conversation (useful for large stable context blocks).
      - Cache stats are accumulated in _session_stats and logged at debug level.
    """

    name = "claude"
    supports_explicit_caching = True
    supports_implicit_caching = False

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> None:
        super().__init__()
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def _check_availability(self) -> bool:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            self._missing_detail = "ANTHROPIC_API_KEY not set"
            return False
        return True

    def fix_suggestion(self) -> str:
        return "Set ANTHROPIC_API_KEY in your .env file"

    def optimize_for_caching(
        self, stable_parts: list[str], dynamic_parts: list[str]
    ) -> list[dict[str, Any]]:
        """Return Anthropic content blocks with cache_control on large stable parts."""
        from orchid import config as cfg

        caching_enabled = cfg.get("caching.enabled", True) and cfg.get(
            "caching.anthropic.cache_control", True
        )
        blocks: list[dict[str, Any]] = []
        for part in stable_parts:
            if not part:
                continue
            block: dict[str, Any] = {"type": "text", "text": part}
            if caching_enabled and len(part) >= _MIN_CACHE_CHARS:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        for part in dynamic_parts:
            if part:
                blocks.append({"type": "text", "text": part})
        return blocks

    def complete(
        self,
        messages: list[Any],
        system: str | None = None,
        cacheable_prefix: int | None = None,
        **kwargs: Any,
    ) -> str:
        import anthropic  # lazy import

        from orchid import config as cfg

        raw = self._normalise_messages(messages)
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

        caching_enabled = cfg.get("caching.enabled", True) and cfg.get(
            "caching.anthropic.cache_control", True
        )

        model = kwargs.pop("model", self.model)
        max_tokens = kwargs.pop("max_tokens", self.max_tokens)
        temperature = kwargs.pop("temperature", self.temperature)

        # System prompt — cache automatically when large enough
        sys_text = system or "You are a helpful assistant."
        if caching_enabled and len(sys_text) >= _MIN_CACHE_CHARS:
            system_param: Any = [
                {"type": "text", "text": sys_text, "cache_control": {"type": "ephemeral"}}
            ]
        else:
            system_param = sys_text

        # Build message list — add cache_control to the first cacheable_prefix entries
        api_messages: list[dict[str, Any]] = []
        for i, msg in enumerate(raw):
            content = msg["content"]
            should_cache = (
                caching_enabled
                and cacheable_prefix is not None
                and i < cacheable_prefix
            )
            if should_cache and isinstance(content, str) and len(content) >= _MIN_CACHE_CHARS:
                api_messages.append(
                    {
                        "role": msg["role"],
                        "content": [
                            {
                                "type": "text",
                                "text": content,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                )
            elif should_cache and isinstance(content, list):
                # Already in block format (from optimize_for_caching) — preserve
                api_messages.append(msg)
            else:
                api_messages.append(msg)

        extra_headers: dict[str, str] = {}
        if caching_enabled:
            extra_headers["anthropic-beta"] = _CACHE_BETA
        # Merge any extra_headers the caller passed
        caller_extra = kwargs.pop("extra_headers", {}) or {}
        extra_headers.update(caller_extra)

        @retry(
            retry=lambda e: isinstance(
                e,
                (
                    anthropic.RateLimitError,
                    anthropic.APIConnectionError,
                    anthropic.APITimeoutError,
                ),
            ),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=60),
            reraise=True,
        )
        def _call() -> str:
            # Debug: log system format and first message cache_control presence
            sys_type = "list" if isinstance(system_param, list) else "string"
            sys_has_cache = (
                isinstance(system_param, list)
                and any(b.get("cache_control") for b in system_param)
            )
            first_msg_cache = (
                isinstance(api_messages[0]["content"], list)
                and any(b.get("cache_control") for b in api_messages[0]["content"])
            ) if api_messages else False
            logger.debug(
                "[anthropic] system=%s cache_control=%s | first_msg_cache=%s | beta_header=%s",
                sys_type, sys_has_cache, first_msg_cache,
                extra_headers.get("anthropic-beta", "NOT SET"),
            )

            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_param,
                messages=api_messages,
                extra_headers=extra_headers if extra_headers else None,
                **kwargs,
            )
            if not response.content:
                raise ProviderError("Empty response content from Claude API")

            # Debug: log full usage object and response headers
            if hasattr(response, "usage"):
                logger.debug("[anthropic] usage: %s", response.usage)
            http_resp = getattr(response, "http_response", None) or getattr(response, "_raw_response", None)
            if http_resp is not None:
                logger.debug("[anthropic] response headers: %s", dict(getattr(http_resp, "headers", {})))

            # Accumulate cache stats
            if caching_enabled and hasattr(response, "usage"):
                usage = response.usage
                cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                _session_stats["cache_writes"] += cache_write
                _session_stats["cache_hits"] += cache_read
                _session_stats["input_tokens_total"] += input_tokens
                _session_stats["input_tokens_cached"] += cache_read
                logger.debug(
                    "[anthropic] cache: write=%d read=%d input=%d",
                    cache_write, cache_read, input_tokens,
                )

            return response.content[0].text

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        try:
            return _call()
        except Exception as exc:
            if api_key and api_key in str(exc):
                sanitized = str(exc).replace(api_key, "***")
                raise type(exc)(sanitized) from None
            raise

    def complete_with_tools(self, messages, tools, dispatch_fn, system=None, max_tokens=4096, max_iterations=10):
        import anthropic as anthropic_sdk
        from orchid.budget.guard import get_env

        client = anthropic_sdk.Anthropic(api_key=get_env("ANTHROPIC_API_KEY"))
        anth_tools = [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("input_schema") or {"type": "object", "properties": {}},
            }
            for t in tools
        ]
        msgs = list(self._normalise_messages(messages))
        sys_text = system or "You are a helpful assistant."

        for _ in range(max_iterations):
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=sys_text,
                tools=anth_tools,
                messages=msgs,
            )
            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
            msgs.append({"role": "assistant", "content": assistant_content})

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if block.type == "text":
                        return block.text
                return ""

            if response.stop_reason != "tool_use":
                break

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                is_err = False
                try:
                    result = dispatch_fn(block.name, block.input or {})
                except Exception as exc:
                    result = f"Error: {exc}"
                    is_err = True
                tr: dict = {"type": "tool_result", "tool_use_id": block.id, "content": str(result)}
                if is_err:
                    tr["is_error"] = True
                tool_results.append(tr)
            msgs.append({"role": "user", "content": tool_results})

        for msg in reversed(msgs):
            if msg.get("role") == "assistant":
                for block in msg.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block["text"]
        return f"[truncated: reached max_iterations={max_iterations}]"
