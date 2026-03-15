"""Unified model caller — routes to Claude API or local llama.cpp."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from orchid import config as cfg

logger = logging.getLogger(__name__)

# Cached availability flags so we don't re-probe on every embed() call
_llama_embed_available: bool | None = None  # None = not yet checked
_st_model: Any = None  # sentence-transformers model cache

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
    model: str    # "claude" | "local"
    reason: str
    source: str   # "cli_flag" | "task_annotation" | "project_config" | "heuristic" | "default"


class Message:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def _claude_client():
    import anthropic  # lazy import
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def _openai_client(base_url: str, api_key: str):
    from openai import OpenAI  # lazy import
    return OpenAI(base_url=base_url, api_key=api_key)


def call(
    messages: list[Message | dict[str, str]],
    model_key: str = "local",
    system: str | None = None,
    **kwargs: Any,
) -> str:
    """Call a model by config key ('claude' or 'local') and return text."""
    model_cfg = cfg.get(f"models.{model_key}", {})
    provider = model_cfg.get("provider", "openai_compat")
    model_name = model_cfg.get("model", "local-model")
    max_tokens = model_cfg.get("max_tokens", 2048)
    temperature = model_cfg.get("temperature", 0.5)

    # Normalise messages
    raw = [m.to_dict() if isinstance(m, Message) else m for m in messages]

    if provider == "anthropic":
        client = _claude_client()
        response = client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or "You are a helpful assistant.",
            messages=raw,
            **kwargs,
        )
        return response.content[0].text

    # openai_compat (llama.cpp or any OpenAI-compatible endpoint)
    base_url = model_cfg.get("base_url", os.environ.get("LLAMA_BASE_URL", "http://localhost:8080/v1"))
    api_key = model_cfg.get("api_key", os.environ.get("LLAMA_API_KEY", "none"))
    client = _openai_client(base_url, api_key)

    if system:
        raw = [{"role": "system", "content": system}] + raw

    response = client.chat.completions.create(
        model=model_name,
        messages=raw,
        max_tokens=max_tokens,
        temperature=temperature,
        **kwargs,
    )
    return response.choices[0].message.content or ""


def embed(text: str) -> list[float]:
    """
    Return an embedding vector for *text*.

    Priority:
      1. llama.cpp /v1/embeddings endpoint (LLAMA_EMBED_URL env var,
         defaults to http://localhost:8081/v1)
      2. sentence-transformers all-MiniLM-L6-v2 (local Python fallback)

    Raises RuntimeError if neither backend is available.
    Never calls OpenAI embeddings.
    """
    global _llama_embed_available, _st_model  # noqa: PLW0603

    # ── Try llama.cpp embeddings endpoint ─────────────────────────────────────
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
                # llama.cpp was previously confirmed working but failed this call.
                # Re-raise to avoid silent dimension mismatch with an ST fallback
                # (nomic-embed-text=768-dim vs all-MiniLM=384-dim would corrupt collections).
                raise RuntimeError(
                    f"llama.cpp embedding endpoint failed after prior success: {exc}"
                ) from exc
            logger.warning(
                "llama.cpp embedding endpoint unavailable (%s): %s — trying fallback.",
                base,
                exc,
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
    """Reset cached backend availability — useful in tests."""
    global _llama_embed_available, _st_model  # noqa: PLW0603
    _llama_embed_available = None
    _st_model = None


def route(
    task_type: str,
    task_model_override: str | None = None,
    cli_override: str | None = None,
    task_title: str = "",
) -> RouteDecision:
    """
    Multi-tier routing. Priority order (highest wins):
      1. CLI flag (cli_override)
      2. Task annotation (task_model_override from model:claude in tasks.md)
      3. Keyword heuristic (if auto mode)
      4. Type-based default from config

    Returns a RouteDecision with model key, reason, and source.
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

    # 3. Keyword escalation (runs when cli_override/task_override is absent or "auto")
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
