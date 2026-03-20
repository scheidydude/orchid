"""OpenAI provider — and any OpenAI-compatible API (OpenRouter, etc.)."""

from __future__ import annotations

import os
from typing import Any

from orchid.errors import ProviderError
from orchid.providers.base import ProviderBase


class OpenAIProvider(ProviderBase):
    """Calls the OpenAI API (or any OpenAI-compatible endpoint).

    Requires OPENAI_API_KEY in the environment.

    To use with OpenRouter, set:
      name = "openrouter"
      base_url = "https://openrouter.ai/api/v1"
      api_key from OPENROUTER_API_KEY
    """

    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str = "https://api.openai.com/v1",
        model: str | None = None,
        embed_model: str = "text-embedding-3-small",
        max_tokens: int = 4096,
        temperature: float = 0.5,
    ) -> None:
        super().__init__()
        self._api_key_env = api_key_env
        self.api_key = api_key or os.environ.get(api_key_env, "")
        self.base_url = base_url
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o")
        self.embed_model = embed_model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def _check_availability(self) -> bool:
        key = self.api_key or os.environ.get(self._api_key_env, "")
        if not key:
            self._missing_detail = f"{self._api_key_env} not set"
            return False
        self.api_key = key
        return True

    def fix_suggestion(self) -> str:
        return f"Set {self._api_key_env} in your .env file"

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
        from openai import OpenAI  # lazy import

        client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        resp = client.embeddings.create(model=self.embed_model, input=text)
        return resp.data[0].embedding


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter — routes to many model providers via one API key."""

    name = "openrouter"

    def __init__(
        self,
        model: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            api_key_env="OPENROUTER_API_KEY",
            base_url="https://openrouter.ai/api/v1",
            model=model or os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4-6"),
            **kwargs,
        )
