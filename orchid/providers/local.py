"""Local llama.cpp provider (OpenAI-compatible endpoint)."""

from __future__ import annotations

import logging
import os
from collections import deque
from typing import Any

from orchid.errors import ProviderError
from orchid.providers.base import ProviderBase

logger = logging.getLogger(__name__)

# Log response keys once per process to diagnose available timings fields
_logged_response_keys: bool = False

# Rolling average configuration for ms/tok cache hit detection
_ROLLING_WINDOW_SIZE: int = 20  # Number of recent samples to track
_COLD_EVAL_MULTIPLIER: float = 2.0  # Threshold = rolling_avg * COLD_EVAL_MULTIPLIER


class LocalProvider(ProviderBase):
    """Calls a local llama.cpp server via its OpenAI-compatible API.

    Requires the server to be running at LLAMA_BASE_URL (default localhost:8080/v1).
    Embeddings are served from LLAMA_EMBED_URL (default localhost:8081/v1).

    Implicit KV caching (D0048): llama.cpp caches attention key-value states for
    identical prompt prefixes.  optimize_for_caching() always puts stable content
    first so the longest possible prefix is shared across calls.  The
    cache_prompt: true hint is sent to instruct the server to keep the KV state.
    """

    name = "local"
    supports_implicit_caching = True

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        embed_url: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.5,
    ) -> None:
        super().__init__()
        self.base_url = base_url or os.environ.get("LLAMA_BASE_URL", "http://localhost:8080/v1")
        self.api_key = api_key or os.environ.get("LLAMA_API_KEY", "none")
        self.model = model or os.environ.get("LLAMA_MODEL", "local-model")
        self.embed_url = embed_url or os.environ.get("LLAMA_EMBED_URL", "http://localhost:8081/v1")
        self.max_tokens = max_tokens
        self.temperature = temperature

        # Embed availability state (mirrors logic from models.py)
        self._embed_available: bool | None = None

        # Rolling average tracking per model for cache hit calibration
        self._ms_per_tok_history: deque[float] = deque(maxlen=_ROLLING_WINDOW_SIZE)
        self._rolling_avg_ms_per_tok: float = 5.0  # Initial estimate (cold eval baseline)

    def _update_rolling_average(self, ms_per_tok: float) -> None:
        """Update rolling average with new ms/tok sample."""
        self._ms_per_tok_history.append(ms_per_tok)
        if len(self._ms_per_tok_history) > 0:
            self._rolling_avg_ms_per_tok = sum(self._ms_per_tok_history) / len(self._ms_per_tok_history)

    def _get_cache_hit_threshold(self) -> float:
        """
        Calculate dynamic cache hit threshold based on rolling average.

        Returns ms/tok threshold below which a prompt eval is considered a cache hit.
        Uses rolling average of recent ms/tok measurements multiplied by a factor
        to distinguish cache hits from cold evals.

        A cache hit typically shows 10-50x speedup over cold evals.
        With rolling_avg_ms_per_tok representing typical cold eval performance,
        we set threshold = rolling_avg * COLD_EVAL_MULTIPLIER to catch cache hits.
        """
        if len(self._ms_per_tok_history) < 3:
            # Not enough samples yet, use conservative default
            return 1.0
        return self._rolling_avg_ms_per_tok / _COLD_EVAL_MULTIPLIER

    def _check_availability(self) -> bool:
        try:
            import httpx  # noqa: PLC0415
            resp = httpx.get(f"{self.base_url}/models", timeout=2.0)
            if resp.status_code < 500:
                return True
            self._missing_detail = f"server returned HTTP {resp.status_code}"
            return False
        except Exception as exc:
            self._missing_detail = f"{self.base_url} unreachable: {exc}"
            logger.debug("LocalProvider unavailable: %s", self._missing_detail)
            return False

    def fix_suggestion(self) -> str:
        return f"Start llama.cpp server at {self.base_url} (set LLAMA_BASE_URL to override)"

    def optimize_for_caching(
        self, stable_parts: list[str], dynamic_parts: list[str]
    ) -> str:
        """Concatenate stable content before dynamic for KV-cache locality."""
        return "\n\n".join(p for p in stable_parts + dynamic_parts if p)

    def complete(
        self,
        messages: list[Any],
        system: str | None = None,
        cacheable_prefix: int | None = None,  # accepted but ignored (KV cache is implicit)
        **kwargs: Any,
    ) -> str:
        global _logged_response_keys  # noqa: PLW0603

        from openai import OpenAI  # lazy import

        from orchid import config as cfg

        raw = self._normalise_messages(messages)
        # Flatten any content blocks (from optimize_for_caching) to plain strings
        flat: list[dict[str, Any]] = []
        for m in raw:
            content = self._flatten_content(m["content"])
            flat.append({"role": m["role"], "content": content})

        if system:
            flat = [{"role": "system", "content": system}] + flat

        client = OpenAI(base_url=self.base_url, api_key=self.api_key)

        # cache_prompt hint: tell llama.cpp to retain the KV state for this prompt
        extra_body: dict[str, Any] = {}
        if cfg.get("caching.enabled", True) and cfg.get(
            "caching.local.cache_prompt_hint", True
        ):
            extra_body["cache_prompt"] = True

        response = client.chat.completions.create(
            model=kwargs.pop("model", self.model),
            messages=flat,
            max_tokens=kwargs.pop("max_tokens", self.max_tokens),
            temperature=kwargs.pop("temperature", self.temperature),
            extra_body=extra_body or None,
            **kwargs,
        )
        if not response.choices:
            raise ProviderError(f"{self.name}: empty choices in response")

        # Extract timings from llama.cpp extension fields.
        # llama.cpp includes a 'timings' object in the response body; the
        # OpenAI SDK surfaces unknown fields in model_extra (Pydantic v2) or
        # as plain attributes — try both, then fall back to __dict__.
        try:
            timings: dict[str, Any] = {}
            # Pydantic v2 model_extra (preferred)
            extra = getattr(response, "model_extra", None) or {}
            if extra:
                if not _logged_response_keys:
                    logger.debug("[local] response keys: %s", list(extra.keys()))
                    _logged_response_keys = True
                timings = extra.get("timings", {}) or {}
            if not timings:
                # Plain attribute fallback (older SDK / non-pydantic path)
                timings = getattr(response, "timings", None) or {}
            if not timings:
                # Last resort: inspect __dict__
                timings = vars(response).get("timings", {}) or {}
            if not timings and not _logged_response_keys:
                logger.debug(
                    "[local] response attrs: %s",
                    [a for a in dir(response) if not a.startswith("_")],
                )
                _logged_response_keys = True

            prompt_ms: float = float(timings.get("prompt_ms", 0) or 0.0)
            prompt_n: int = int(timings.get("prompt_n", 0) or 0)

            if prompt_n > 0:
                ms_per_token = prompt_ms / max(prompt_n, 1)

                # Update rolling average with new sample
                self._update_rolling_average(ms_per_token)

                # Get dynamic threshold based on rolling average
                threshold = self._get_cache_hit_threshold()
                is_likely_cache_hit = ms_per_token < threshold

                logger.debug(
                    "[local] %d tokens, %.1fms total, %.2fms/tok (rolling_avg=%.2fms/tok, threshold=%.2fms/tok) — %s",
                    prompt_n,
                    prompt_ms,
                    ms_per_token,
                    self._rolling_avg_ms_per_tok,
                    threshold,
                    "cache hit" if is_likely_cache_hit else "cold eval",
                )

                # Update session cache stats if a session is active
                from orchid.session import get_current_session
                session = get_current_session()
                if session is not None:
                    if is_likely_cache_hit:
                        session.cache_stats["local_fast_evals"] += 1
                    else:
                        session.cache_stats["local_slow_evals"] += 1
                    session.cache_stats["local_prompt_tokens"] += prompt_n
                    session.cache_stats["local_prompt_ms"] += int(prompt_ms)
                    # Track rolling average stats in session for reporting
                    session.cache_stats["local_rolling_avg_ms_per_tok"] = round(
                        self._rolling_avg_ms_per_tok, 3
                    )

        except Exception as exc:
            logger.debug("[local] could not extract timings: %s", exc)

        return response.choices[0].message.content or ""

    def embed(self, text: str) -> list[float]:
        """Try llama.cpp embeddings endpoint; raises RuntimeError on failure."""
        if self._embed_available is not False:
            model_name = os.environ.get("EMBED_MODEL", "nomic-embed-text")
            try:
                import httpx  # noqa: PLC0415
                resp = httpx.post(
                    f"{self.embed_url}/embeddings",
                    json={"model": model_name, "input": text},
                    timeout=10.0,
                )
                resp.raise_for_status()
                vec = resp.json()["data"][0]["embedding"]
                if self._embed_available is None:
                    logger.debug("Embed backend: llama.cpp (%s)", self.embed_url)
                    self._embed_available = True
                return vec
            except Exception as exc:
                if self._embed_available is True:
                    raise RuntimeError(
                        f"llama.cpp embedding endpoint failed after prior success: {exc}"
                    ) from exc
                logger.warning("llama.cpp embed unavailable: %s — falling through.", exc)
                self._embed_available = False

        raise RuntimeError(
            "llama.cpp embedding endpoint not available. "
            "Use sentence-transformers fallback via models.embed()."
        )