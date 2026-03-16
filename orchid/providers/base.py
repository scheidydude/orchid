"""ProviderBase — abstract base class for all model providers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any


class ProviderUnavailableError(Exception):
    """Raised when a required provider cannot be contacted or is not configured."""

    def __init__(self, provider_name: str, missing: str, suggestion: str = "") -> None:
        self.provider_name = provider_name
        self.missing = missing
        self.suggestion = suggestion
        msg = f"Provider '{provider_name}' unavailable: {missing}"
        if suggestion:
            msg += f"\n  Fix: {suggestion}"
        super().__init__(msg)


class ProviderBase(ABC):
    """Abstract base for a model backend.

    Subclasses implement _check_availability() and complete().
    is_available() caches the probe result for 60 seconds.
    """

    name: str = ""
    _CACHE_TTL: float = 60.0

    def __init__(self) -> None:
        self._availability_cache: bool | None = None
        self._availability_checked_at: float = 0.0
        self._missing_detail: str = ""

    # ── Availability ──────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """Return True if the provider is reachable/configured. Result cached 60 s."""
        now = time.monotonic()
        if (
            self._availability_cache is not None
            and (now - self._availability_checked_at) < self._CACHE_TTL
        ):
            return self._availability_cache
        self._availability_cache = self._check_availability()
        self._availability_checked_at = now
        return self._availability_cache

    def reset_availability_cache(self) -> None:
        self._availability_cache = None

    @abstractmethod
    def _check_availability(self) -> bool:
        """Subclass implements: return True if provider is ready."""

    def availability_detail(self) -> str:
        """Human-readable reason why the provider is unavailable."""
        return self._missing_detail or "unknown reason"

    def fix_suggestion(self) -> str:
        """Human-readable suggestion for making the provider available."""
        return ""

    # ── Inference ─────────────────────────────────────────────────────────────

    @abstractmethod
    def complete(
        self,
        messages: list[Any],
        system: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Send messages and return the response text."""

    # ── Embeddings ────────────────────────────────────────────────────────────

    def embed(self, text: str) -> list[float]:
        """Return embedding vector. Raises NotImplementedError if unsupported."""
        raise NotImplementedError(f"Provider '{self.name}' does not support embeddings.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_messages(messages: list[Any]) -> list[dict[str, str]]:
        """Convert Message objects or raw dicts to {"role": ..., "content": ...} dicts."""
        result = []
        for m in messages:
            if hasattr(m, "to_dict"):
                result.append(m.to_dict())
            elif isinstance(m, dict):
                result.append(m)
            else:
                result.append({"role": "user", "content": str(m)})
        return result

    def __repr__(self) -> str:
        available = "✅" if self.is_available() else "❌"
        return f"<{self.__class__.__name__} name={self.name!r} {available}>"
