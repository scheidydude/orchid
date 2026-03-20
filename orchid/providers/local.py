"""Local llama.cpp provider (OpenAI-compatible endpoint)."""

from __future__ import annotations

import logging
import os
from typing import Any

from orchid.errors import ProviderError
from orchid.providers.base import ProviderBase

logger = logging.getLogger(__name__)


class LocalProvider(ProviderBase):
    """Calls a local llama.cpp server via its OpenAI-compatible /v1 API.

    Requires the server to be running at LLAMA_BASE_URL (default localhost:8080/v1).
    Embeddings are served from LLAMA_EMBED_URL (default localhost:8081/v1).
    """

    name = "local"

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

    def complete(
        self,
        messages: list[Any],
        system: str | None = None,
        **kwargs: Any,
    ) -> str:
        from openai import OpenAI  # lazy import

        raw = self._normalise_messages(messages)
        if system:
            raw = [{"role": "system", "content": system}] + raw

        client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        response = client.chat.completions.create(
            model=kwargs.pop("model", self.model),
            messages=raw,
            max_tokens=kwargs.pop("max_tokens", self.max_tokens),
            temperature=kwargs.pop("temperature", self.temperature),
            **kwargs,
        )
        if not response.choices:
            raise ProviderError(f"{self.name}: empty choices in response")
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
