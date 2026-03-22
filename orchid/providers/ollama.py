"""Ollama provider — local inference via Ollama's OpenAI-compatible API."""

from __future__ import annotations

import logging
import os
from typing import Any

from orchid.errors import ProviderError
from orchid.providers.base import ProviderBase

logger = logging.getLogger(__name__)


class OllamaProvider(ProviderBase):
    """Calls an Ollama server.

    Ollama exposes an OpenAI-compatible /v1 endpoint for chat completions
    and its own /api/embeddings endpoint for embeddings.

    Requires Ollama running at OLLAMA_BASE_URL (default http://localhost:11434).
    """

    name = "ollama"
    supports_implicit_caching = True

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__()
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:32b")

    def _check_availability(self) -> bool:
        try:
            import httpx  # noqa: PLC0415
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=2.0)
            if resp.status_code == 200:
                return True
            self._missing_detail = f"Ollama returned HTTP {resp.status_code}"
            return False
        except Exception as exc:
            self._missing_detail = f"{self.base_url} unreachable: {exc}"
            logger.debug("OllamaProvider unavailable: %s", self._missing_detail)
            return False

    def fix_suggestion(self) -> str:
        return f"Start Ollama and ensure it listens at {self.base_url} (set OLLAMA_BASE_URL to override)"

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
        from openai import OpenAI  # lazy import — Ollama speaks OpenAI protocol

        raw = self._normalise_messages(messages)
        # Flatten any content blocks (from optimize_for_caching) to plain strings
        flat: list[dict[str, Any]] = []
        for m in raw:
            content = self._flatten_content(m["content"])
            flat.append({"role": m["role"], "content": content})

        if system:
            flat = [{"role": "system", "content": system}] + flat

        client = OpenAI(base_url=f"{self.base_url}/v1", api_key="ollama")
        response = client.chat.completions.create(
            model=kwargs.pop("model", self.model),
            messages=flat,
            **kwargs,
        )
        if not response.choices:
            raise ProviderError(f"{self.name}: empty choices in response")
        return response.choices[0].message.content or ""

    def embed(self, text: str) -> list[float]:
        import httpx  # noqa: PLC0415

        resp = httpx.post(
            f"{self.base_url}/api/embeddings",
            json={"model": self.model, "prompt": text},
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
