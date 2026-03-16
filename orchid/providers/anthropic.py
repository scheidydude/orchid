"""Anthropic Claude provider."""

from __future__ import annotations

import os
from typing import Any

from orchid.providers.base import ProviderBase


class AnthropicProvider(ProviderBase):
    """Calls the Anthropic Claude API.

    Requires ANTHROPIC_API_KEY in the environment.
    """

    name = "claude"

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

    def complete(
        self,
        messages: list[Any],
        system: str | None = None,
        **kwargs: Any,
    ) -> str:
        import anthropic  # lazy import

        raw = self._normalise_messages(messages)
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model=kwargs.pop("model", self.model),
            max_tokens=kwargs.pop("max_tokens", self.max_tokens),
            temperature=kwargs.pop("temperature", self.temperature),
            system=system or "You are a helpful assistant.",
            messages=raw,
            **kwargs,
        )
        return response.content[0].text
