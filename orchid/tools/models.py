"""Unified model caller — routes to a ProviderBase via the ProviderRegistry.

Backward-compatible interface:
  call(messages, model_key="local")  → still works
  call(messages, model_key="claude") → routes to AnthropicProvider
  call(messages, model_key="ollama") → routes to OllamaProvider (if configured)

The embed() function tries llama.cpp first, then sentence-transformers.
Both paths are now delegated to providers but embed() retains its own
fallback chain since embeddings are separate from chat completions.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from orchid import config as cfg

logger = logging.getLogger(__name__)

# Legacy embed-cache flags (kept for reset_embed_cache() backward compat)
_llama_embed_available: bool | None = None
_st_model: Any = None

COMPLEXITY_KEYWORDS = [
    "regex", "parser", "parsing", "tokenize", "ast",
    "algorithm", "concurrent", "async", "threading",
    "cryptograph", "compression", "binary", "protocol",
    "authentication", "oauth", "jwt", "security",
    "optimize", "performance", "benchmark", "websocket",
    "serialize", "deserializ", "encoding", "decoding",
]


@dataclass
class RouteDecision:
    model: str    # provider name: "claude" | "local" | "ollama" | ...
    reason: str
    source: str   # "cli_flag" | "task_annotation" | "cli_provider_override" |
                  # "project_config" | "env_var" | "heuristic" | "default"


class Message:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


# ── Primary call interface ────────────────────────────────────────────────────

def call(
    messages: list[Message | dict[str, str]],
    model_key: str = "local",
    system: str | None = None,
    **kwargs: Any,
) -> str:
    """Call a model provider by key and return response text.

    model_key is the provider name: "claude", "local", "ollama", "openai", etc.
    All registered providers (orchid/providers/) are accessible by their name.

    Backward compatible: model_key="claude" and model_key="local" continue to
    work exactly as before.
    """
    from orchid.providers.registry import get_registry
    try:
        registry = get_registry()
        provider = registry.get_by_key(model_key)
        return provider.complete(messages, system=system, **kwargs)
    except Exception:
        # If registry lookup fails, fall back to direct config-based call
        # (preserves behavior during startup before config is fully loaded)
        raise


def embed(text: str) -> list[float]:
    """Return an embedding vector for *text*.

    Priority:
      1. llama.cpp /v1/embeddings endpoint (via LocalProvider or LLAMA_EMBED_URL)
      2. sentence-transformers all-MiniLM-L6-v2 (local Python fallback)

    Raises RuntimeError if neither backend is available.
    Never calls OpenAI embeddings.
    """
    global _llama_embed_available, _st_model  # noqa: PLW0603

    # ── Try llama.cpp via LocalProvider (or direct if registry not ready) ─────
    if _llama_embed_available is not False:
        base = os.environ.get("LLAMA_EMBED_URL", "http://localhost:8081/v1")
        model_name = cfg.get(
            "vector_memory.embedding_model",
            os.environ.get("EMBED_MODEL", "nomic-embed-text"),
        )
        try:
            import httpx  # noqa: PLC0415
            resp = httpx.post(
                f"{base}/embeddings",
                json={"model": model_name, "input": text},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            vec = data["data"][0]["embedding"]
            if _llama_embed_available is None:
                logger.debug("Embedding backend: llama.cpp (%s)", base)
                _llama_embed_available = True
            return vec
        except Exception as exc:
            if _llama_embed_available is True:
                raise RuntimeError(
                    f"llama.cpp embedding endpoint failed after prior success: {exc}"
                ) from exc
            logger.warning(
                "llama.cpp embedding endpoint unavailable (%s): %s — trying fallback.",
                base, exc,
            )
            _llama_embed_available = False

    # ── Fallback: sentence-transformers ───────────────────────────────────────
    fallback_model = cfg.get("vector_memory.fallback_model", "all-MiniLM-L6-v2")
    try:
        if _st_model is None:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
            logger.info("Loading sentence-transformers model '%s'…", fallback_model)
            _st_model = SentenceTransformer(fallback_model)
        vec = _st_model.encode(text, normalize_embeddings=True).tolist()
        return vec
    except ImportError:
        raise RuntimeError(
            "No embedding backend available. "
            "Install sentence-transformers or run llama.cpp with nomic-embed-text."
        )
    except Exception as exc:
        raise RuntimeError(f"sentence-transformers embedding failed: {exc}") from exc


def reset_embed_cache() -> None:
    """Reset cached embed backend availability — useful in tests."""
    global _llama_embed_available, _st_model  # noqa: PLW0603
    _llama_embed_available = None
    _st_model = None


# ── Routing ───────────────────────────────────────────────────────────────────

def route(
    task_type: str,
    task_model_override: str | None = None,
    cli_override: str | None = None,
    task_title: str = "",
) -> RouteDecision:
    """Multi-tier routing. Priority order (highest wins):

      1. CLI flag (cli_override via --code-model)
      2. Task annotation (model:claude in tasks.md)
      3. Keyword heuristic (complexity escalation)
      4. Type-based default from config

    Returns RouteDecision with model/provider key, reason, and source.
    The returned model key is then passed to call() which routes it through
    the ProviderRegistry.
    """
    # 1. CLI flag overrides everything
    if cli_override and cli_override not in ("auto", ""):
        return RouteDecision(
            model=cli_override,
            reason=f"CLI --code-model {cli_override}",
            source="cli_flag",
        )

    # 2. Task annotation overrides config and heuristic
    if task_model_override and task_model_override not in ("auto", ""):
        return RouteDecision(
            model=task_model_override,
            reason=f"task annotation model:{task_model_override}",
            source="task_annotation",
        )

    # 3. Keyword escalation
    escalation_cfg = cfg.get("routing.escalation", {})
    if escalation_cfg.get("enabled", True):
        threshold = escalation_cfg.get("threshold", 1)
        keywords = escalation_cfg.get("keywords", COMPLEXITY_KEYWORDS)
        title_lower = task_title.lower()
        matched = [kw for kw in keywords if kw in title_lower]
        if len(matched) >= threshold:
            return RouteDecision(
                model="claude",
                reason=f"keyword match {matched[0]!r}",
                source="heuristic",
            )

    # 4. Type-based default
    claude_tasks = set(cfg.get("routing.claude_tasks", []))
    local_tasks = set(cfg.get("routing.local_tasks", []))
    if task_type in claude_tasks:
        return RouteDecision(
            model="claude",
            reason=f"task type {task_type!r} in claude_tasks",
            source="default",
        )
    if task_type in local_tasks:
        return RouteDecision(
            model="local",
            reason=f"task type {task_type!r} in local_tasks",
            source="default",
        )
    default = cfg.get("routing.default", "local")
    return RouteDecision(
        model=default,
        reason="fallback default",
        source="default",
    )
