"""Tests for the Provider Registry (M3.4)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from orchid.providers.base import ProviderBase, ProviderUnavailableError
from orchid.providers.registry import (
    ProviderRegistry,
    _AGENT_DEFAULTS,
    _TASK_TYPE_DEFAULTS,
    get_registry,
    reset_registry,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


class _AlwaysOnProvider(ProviderBase):
    name = "always_on"

    def _check_availability(self) -> bool:
        return True

    def complete(self, messages, system=None, **kwargs):
        return "ok"


class _AlwaysOffProvider(ProviderBase):
    name = "always_off"

    def _check_availability(self) -> bool:
        self._missing_detail = "intentionally unavailable"
        return False

    def fix_suggestion(self) -> str:
        return "flip the switch"

    def complete(self, messages, system=None, **kwargs):
        return "never called"


@pytest.fixture(autouse=True)
def _reset_registry():
    """Reset registry singleton before and after each test."""
    reset_registry()
    yield
    reset_registry()


# ── 1. ProviderUnavailableError ────────────────────────────────────────────────


def test_provider_unavailable_error_attrs():
    err = ProviderUnavailableError("myp", "key missing", "set the key")
    assert err.provider_name == "myp"
    assert err.missing == "key missing"
    assert err.suggestion == "set the key"
    assert "myp" in str(err)


def test_provider_unavailable_error_no_suggestion():
    err = ProviderUnavailableError("myp", "key missing")
    assert err.suggestion == ""
    assert "key missing" in str(err)


# ── 2. ProviderBase caching ────────────────────────────────────────────────────


def test_availability_cache():
    p = _AlwaysOnProvider()
    p._CACHE_TTL = 60.0
    # First call hits _check_availability
    assert p.is_available() is True
    # Cached result returned without re-calling
    p._check_availability = MagicMock(return_value=False)
    assert p.is_available() is True  # still cached


def test_availability_cache_reset():
    p = _AlwaysOnProvider()
    assert p.is_available() is True
    p.reset_availability_cache()
    p._check_availability = MagicMock(return_value=False)
    assert p.is_available() is False


# ── 3. ProviderRegistry loading ───────────────────────────────────────────────


def _load_registry_patched():
    """Load registry with cfg.get patched to return empty dicts."""
    with patch("orchid.config.get", return_value={}):
        return ProviderRegistry.load()


def test_registry_always_has_claude_and_local():
    registry = _load_registry_patched()
    assert "claude" in registry._providers
    assert "local" in registry._providers


def test_registry_get_by_key_known():
    registry = _load_registry_patched()
    registry._providers["claude"] = _AlwaysOnProvider()
    p = registry.get_by_key("claude")
    assert isinstance(p, _AlwaysOnProvider)


def test_registry_get_by_key_unknown_raises():
    registry = _load_registry_patched()
    with pytest.raises(ProviderUnavailableError) as exc_info:
        registry.get_by_key("nonexistent")
    assert "nonexistent" in str(exc_info.value)


def test_registry_get_unavailable_provider_raises():
    registry = _load_registry_patched()
    registry._providers["broken"] = _AlwaysOffProvider()
    with pytest.raises(ProviderUnavailableError) as exc_info:
        registry.get_by_key("broken")
    assert "broken" in str(exc_info.value)


# ── 4. resolve_name — 5-layer chain ──────────────────────────────────────────


def test_resolve_name_cli_override_wins():
    registry = _load_registry_patched()
    with patch("orchid.config.get", return_value={}):
        name = registry.resolve_name("developer", cli_override="ollama")
    assert name == "ollama"


def test_resolve_name_project_config():
    def _fake_get(key, default=None):
        if key == "providers.developer":
            return "openai"
        return default

    registry = _load_registry_patched()
    with patch("orchid.config.get", side_effect=_fake_get):
        name = registry.resolve_name("developer")
    assert name == "openai"


def test_resolve_name_env_var(monkeypatch):
    monkeypatch.setenv("ORCHID_DEVELOPER_PROVIDER", "bedrock")
    registry = _load_registry_patched()
    with patch("orchid.config.get", return_value={}):
        name = registry.resolve_name("developer")
    assert name == "bedrock"
    monkeypatch.delenv("ORCHID_DEVELOPER_PROVIDER")


def test_resolve_name_task_type_default():
    registry = _load_registry_patched()
    with patch("orchid.config.get", return_value={}):
        name = registry.resolve_name("base", task_type="orchestrate")
    assert name == _TASK_TYPE_DEFAULTS["orchestrate"]


def test_resolve_name_agent_type_default():
    registry = _load_registry_patched()
    with patch("orchid.config.get", return_value={}):
        name = registry.resolve_name("orchestrator")
    assert name == _AGENT_DEFAULTS["orchestrator"]


def test_resolve_name_offline_mode_returns_local():
    registry = _load_registry_patched()
    registry.set_offline(True)
    with patch("orchid.config.get", return_value={}):
        name = registry.resolve_name("orchestrator", cli_override="claude")
    assert name == "local"


# ── 5. AnthropicProvider ──────────────────────────────────────────────────────


def test_anthropic_available_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    from orchid.providers.anthropic import AnthropicProvider
    p = AnthropicProvider()
    p.reset_availability_cache()
    assert p.is_available() is True


def test_anthropic_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from orchid.providers.anthropic import AnthropicProvider
    p = AnthropicProvider()
    p.reset_availability_cache()
    assert p.is_available() is False
    assert "ANTHROPIC_API_KEY" in p.fix_suggestion()


# ── 6. OllamaProvider ─────────────────────────────────────────────────────────


def test_ollama_available_when_server_up():
    from orchid.providers.ollama import OllamaProvider
    p = OllamaProvider()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.get", return_value=mock_resp):
        p.reset_availability_cache()
        assert p.is_available() is True


def test_ollama_unavailable_when_server_down():
    from orchid.providers.ollama import OllamaProvider
    p = OllamaProvider()
    with patch("httpx.get", side_effect=ConnectionError("refused")):
        p.reset_availability_cache()
        assert p.is_available() is False


# ── 7. all_status ─────────────────────────────────────────────────────────────


def test_all_status_returns_list():
    registry = _load_registry_patched()
    registry._providers["on"] = _AlwaysOnProvider()
    registry._providers["off"] = _AlwaysOffProvider()
    statuses = registry.all_status()
    names = {s["name"] for s in statuses}
    assert "on" in names
    assert "off" in names
    off_entry = next(s for s in statuses if s["name"] == "off")
    assert off_entry["available"] is False
    assert "fix" in off_entry


# ── 8. models.call() backward compat ──────────────────────────────────────────


def test_models_call_routes_to_registry():
    from orchid.tools.models import call

    mock_provider = MagicMock()
    mock_provider.is_available.return_value = True
    mock_provider.complete.return_value = "response"

    mock_registry = MagicMock()
    mock_registry.get_by_key.return_value = mock_provider

    with patch("orchid.providers.registry.get_registry", return_value=mock_registry):
        result = call([{"role": "user", "content": "hello"}], model_key="claude")

    assert result == "response"
    mock_registry.get_by_key.assert_called_once_with("claude")
