"""Orchid exception hierarchy."""

from __future__ import annotations


class OrchidError(Exception):
    """Base class for all Orchid-specific errors."""


class ProviderError(OrchidError):
    """Raised by provider backends on non-retryable failures (empty response, auth, etc.)."""


class ToolError(OrchidError):
    """Raised by shell/filesystem tools on blocked or invalid operations."""
