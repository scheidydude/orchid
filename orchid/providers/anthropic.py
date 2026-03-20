"""Anthropic Claude provider."""

from __future__ import annotations

import os
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from orchid.errors import ProviderError
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

        model = kwargs.pop("model", self.model)
        max_tokens = kwargs.pop("max_tokens", self.max_tokens)
        temperature = kwargs.pop("temperature", self.temperature)

        @retry(
            retry=lambda e: isinstance(e, (
                anthropic.RateLimitError,
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
            )),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=60),
            reraise=True,
        )
        def _call() -> str:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system or "You are a helpful assistant.",
                messages=raw,
                **kwargs,
            )
            if not response.content:
                raise ProviderError("Empty response content from Claude API")
            return response.content[0].text

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        try:
            return _call()
        except Exception as exc:
            if api_key and api_key in str(exc):
                sanitized = str(exc).replace(api_key, "***")
                raise type(exc)(sanitized) from None
            raise
