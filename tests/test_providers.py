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


def test_resolve_name_task_model_annotation():
    """Task model: annotation (layer 4) is used when no project config override exists."""
    registry = _load_registry_patched()
    with patch("orchid.config.get", return_value={}):
        name = registry.resolve_name("discussion", task_model="claude")
    assert name == "claude"


# ── 4b. Per-agent provider overrides (T087) ───────────────────────────────────


def test_per_agent_provider_override_discussion():
    """providers.discussion: local in .orchid.yaml overrides the default claude."""
    def _fake_get(key, default=None):
        if key == "providers.discussion":
            return "local"
        return default

    registry = _load_registry_patched()
    with patch("orchid.config.get", side_effect=_fake_get):
        name = registry.resolve_name("discussion")
    assert name == "local"


def test_per_agent_provider_override_reviewer_stays_claude():
    """reviewer defaults to claude from _AGENT_DEFAULTS when no config override."""
    registry = _load_registry_patched()
    with patch("orchid.config.get", return_value={}):
        name = registry.resolve_name("reviewer")
    assert name == _AGENT_DEFAULTS["reviewer"]
    assert name == "claude"


def test_per_agent_override_beats_type_default():
    """providers.developer: claude beats code_generate task-type default of local."""
    def _fake_get(key, default=None):
        if key == "providers.developer":
            return "claude"
        return default

    registry = _load_registry_patched()
    with patch("orchid.config.get", side_effect=_fake_get):
        name = registry.resolve_name("developer", task_type="code_generate")
    assert name == "claude"


def test_cli_flag_beats_per_agent_override():
    """CLI --provider flag (layer 1) beats providers.<agent_name> from config (layer 2)."""
    def _fake_get(key, default=None):
        if key == "providers.discussion":
            return "local"
        return default

    registry = _load_registry_patched()
    with patch("orchid.config.get", side_effect=_fake_get):
        name = registry.resolve_name("discussion", cli_override="ollama")
    assert name == "ollama"


def test_project_config_beats_task_annotation():
    """Project .orchid.yaml providers (layer 2) wins over task model: annotation (layer 4)."""
    def _fake_get(key, default=None):
        if key == "providers.reviewer":
            return "local"
        return default

    registry = _load_registry_patched()
    with patch("orchid.config.get", side_effect=_fake_get):
        name = registry.resolve_name("reviewer", task_model="claude")
    assert name == "local"


def test_rollup_respects_project_provider_override():
    """Rollup uses providers.task_types.rollup override if set in project config."""
    def _fake_get(key, default=None):
        if key == "providers.task_types.rollup":
            return "local"
        return default

    registry = _load_registry_patched()
    with patch("orchid.config.get", side_effect=_fake_get):
        name = registry.resolve_name("base", task_type="rollup")
    assert name == "local"


def test_rollup_default_comes_from_task_type_defaults():
    """Rollup falls through to providers.task_type_defaults.rollup (no special-case code)."""
    def _fake_get(key, default=None):
        if key == "providers.task_type_defaults.rollup":
            return "claude"
        return default

    registry = _load_registry_patched()
    with patch("orchid.config.get", side_effect=_fake_get):
        name = registry.resolve_name("base", task_type="rollup")
    assert name == "claude"


def test_agent_defaults_config_driven_layer7():
    """Layer 7 reads from providers.agent_defaults in config, not hardcoded Python only."""
    def _fake_get(key, default=None):
        if key == "providers.agent_defaults.orchestrator":
            return "bedrock"
        return default

    registry = _load_registry_patched()
    with patch("orchid.config.get", side_effect=_fake_get):
        name = registry.resolve_name("orchestrator")
    assert name == "bedrock"


def test_task_type_defaults_config_driven_layer6():
    """Layer 6 reads from providers.task_type_defaults in config, not hardcoded Python only."""
    def _fake_get(key, default=None):
        if key == "providers.task_type_defaults.review":
            return "ollama"
        return default

    registry = _load_registry_patched()
    with patch("orchid.config.get", side_effect=_fake_get):
        name = registry.resolve_name("reviewer", task_type="review")
    # layer 2 (providers.reviewer) returns nothing; layer 6 config should win
    assert name == "ollama"


def test_python_dict_fallback_when_config_missing():
    """When config returns nothing for agent_defaults, Python dict fallback is used."""
    registry = _load_registry_patched()
    with patch("orchid.config.get", return_value={}):
        assert registry.resolve_name("reviewer") == _AGENT_DEFAULTS["reviewer"]
        assert registry.resolve_name("developer") == _AGENT_DEFAULTS["developer"]
        assert registry.resolve_name("unknown_agent") == "local"


def test_cli_flag_still_beats_project_config():
    """CLI --provider flag (layer 1) overrides project .orchid.yaml providers (layer 2)."""
    def _fake_get(key, default=None):
        if key == "providers.orchestrator":
            return "local"
        return default

    registry = _load_registry_patched()
    with patch("orchid.config.get", side_effect=_fake_get):
        name = registry.resolve_name("orchestrator", cli_override="bedrock")
    assert name == "bedrock"


def test_orchestrator_respects_project_provider_override():
    """_plan_task() routes through the provider registry, not hardcoded 'claude'.

    When .orchid.yaml sets providers.orchestrator: local the planning call
    must use 'local', not 'claude'.  Also verifies that a cli_provider_overrides
    entry for 'orchestrator' takes priority over the project config.
    """
    from dataclasses import dataclass, field
    from pathlib import Path
    from unittest.mock import MagicMock, patch

    from orchid.memory.state import Task
    from orchid.orchestrator import Orchestrator

    # Minimal Task for planning
    task = Task(id="T001", title="Build widget", description="Make a widget.")

    # Minimal Session mock — only context_block() is called by _plan_task
    mock_session = MagicMock()
    mock_session.project_dir = Path("/tmp/fake-project")
    mock_session.context_block.return_value = ""

    def _project_cfg(key, default=None):
        if key == "providers.orchestrator":
            return "local"
        return default

    # Case 1: project config override — should use 'local' not 'claude'
    with (
        patch("orchid.config.get", side_effect=_project_cfg),
        patch("orchid.orchestrator.call", return_value="plan text") as mock_call,
        patch("orchid.providers.registry.get_registry", return_value=_load_registry_patched()),
    ):
        orch = Orchestrator(session=mock_session)
        result = orch._plan_task(task)

    assert result == "plan text"
    _, kwargs = mock_call.call_args
    assert kwargs["model_key"] == "local", (
        f"Expected model_key='local' (from providers.orchestrator), got {kwargs['model_key']!r}"
    )

    # Case 2: cli_provider_overrides beats project config
    with (
        patch("orchid.config.get", side_effect=_project_cfg),
        patch("orchid.orchestrator.call", return_value="plan text") as mock_call,
        patch("orchid.providers.registry.get_registry", return_value=_load_registry_patched()),
    ):
        orch = Orchestrator(session=mock_session, cli_provider_overrides={"orchestrator": "ollama"})
        orch._plan_task(task)

    _, kwargs = mock_call.call_args
    assert kwargs["model_key"] == "ollama", (
        f"Expected model_key='ollama' (CLI override), got {kwargs['model_key']!r}"
    )


# ── 4b. session / slack_bot / cli registry routing ────────────────────────────


def test_session_hot_memory_compression_respects_provider_config():
    """_maybe_compress_hot_memory uses resolve_name('base'), not hardcoded 'claude'.

    When providers.base is overridden in config the compression call must use it.
    """
    from unittest.mock import MagicMock, patch

    from orchid.session import Session

    def _cfg(key, default=None):
        if key == "providers.base":
            return "local"
        if key == "memory.compression_threshold":
            return 5  # very low so compression triggers
        return default

    session = MagicMock(spec=Session)
    session.hot_memory = "x" * 10  # exceeds threshold of 5

    with (
        patch("orchid.config.get", side_effect=_cfg),
        patch("orchid.tools.models.call", return_value="compressed") as mock_call,
        patch("orchid.providers.registry.get_registry", return_value=_load_registry_patched()),
    ):
        # Call the real method on the real class, but with our mock session as self
        Session._maybe_compress_hot_memory(session)

    _, kwargs = mock_call.call_args
    assert kwargs["model_key"] == "local", (
        f"Expected 'local' from providers.base config, got {kwargs['model_key']!r}"
    )


def test_session_hot_memory_offline_uses_local():
    """When registry is in offline mode, compression uses 'local'."""
    from unittest.mock import MagicMock, patch

    from orchid.session import Session

    offline_registry = _load_registry_patched()
    offline_registry.set_offline(True)

    session = MagicMock(spec=Session)
    session.hot_memory = "x" * 100

    with (
        patch("orchid.config.get", side_effect=lambda k, d=None: 5 if k == "memory.compression_threshold" else d),
        patch("orchid.tools.models.call", return_value="compressed") as mock_call,
        patch("orchid.providers.registry.get_registry", return_value=offline_registry),
    ):
        Session._maybe_compress_hot_memory(session)

    _, kwargs = mock_call.call_args
    assert kwargs["model_key"] == "local"


def test_slack_intent_parsing_respects_provider_config():
    """_parse_intent LLM fallback uses resolve_name('base'), not hardcoded 'claude'.

    We pass a message that doesn't match any rule-based pattern so the LLM path runs.
    """
    from unittest.mock import MagicMock, patch

    def _cfg(key, default=None):
        if key == "providers.base":
            return "local"
        return default

    with (
        patch("orchid.config.get", side_effect=_cfg),
        patch("orchid.tools.models.call", return_value='{"intent": "status", "arg": ""}') as mock_call,
        patch("orchid.providers.registry.get_registry", return_value=_load_registry_patched()),
    ):
        from orchid.interfaces.slack_bot import SlackBot
        bot = MagicMock(spec=SlackBot)
        # Use a message that bypasses all rule-based patterns, hitting the LLM fallback
        result = SlackBot._parse_intent(bot, "I'd like to know what's going on with the project")

    assert mock_call.called, "LLM fallback was never reached (rule-based matched first)"
    _, kwargs = mock_call.call_args
    assert kwargs["model_key"] == "local", (
        f"Expected 'local' from providers.base config, got {kwargs['model_key']!r}"
    )


def test_cli_interactive_uses_registry_default(tmp_path):
    """_cmd_interactive with no model arg uses resolve_name('base'), not hardcoded 'claude'."""
    from unittest.mock import MagicMock, patch

    from orchid.interfaces.cli import _cmd_interactive

    def _cfg(key, default=None):
        if key == "providers.base":
            return "local"
        return default

    mock_session = MagicMock()
    mock_session.project_name = "test"
    mock_session.context_block.return_value = ""

    captured_model_key: list[str] = []

    def _fake_agent_run(self, prompt):
        captured_model_key.append(self.model_key)
        return "response"

    with (
        patch("orchid.config.get", side_effect=_cfg),
        patch("orchid.providers.registry.get_registry", return_value=_load_registry_patched()),
        patch("orchid.interfaces.cli._make_session", return_value=mock_session),
        patch("orchid.agents.base.BaseAgent.run", _fake_agent_run),
        patch("orchid.interfaces.cli.Prompt.ask", side_effect=["hello", EOFError]),
        patch("orchid.interfaces.cli.console"),
    ):
        _cmd_interactive(str(tmp_path))

    assert captured_model_key, "Agent.run was never called"
    assert captured_model_key[0] == "local", (
        f"Expected model_key='local' from registry, got {captured_model_key[0]!r}"
    )


def test_cli_interactive_honours_explicit_model(tmp_path):
    """_cmd_interactive with model='ollama' uses that directly without registry lookup."""
    from unittest.mock import MagicMock, patch

    from orchid.interfaces.cli import _cmd_interactive

    mock_session = MagicMock()
    mock_session.project_name = "test"
    mock_session.context_block.return_value = ""

    captured_model_key: list[str] = []

    def _fake_agent_run(self, prompt):
        captured_model_key.append(self.model_key)
        return "response"

    with (
        patch("orchid.interfaces.cli._make_session", return_value=mock_session),
        patch("orchid.agents.base.BaseAgent.run", _fake_agent_run),
        patch("orchid.interfaces.cli.Prompt.ask", side_effect=["hello", EOFError]),
        patch("orchid.interfaces.cli.console"),
    ):
        _cmd_interactive(str(tmp_path), model="ollama")

    assert captured_model_key[0] == "ollama"


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


# ── 8. Empty response.choices guards ─────────────────────────────────────────


def _mock_openai_client(empty_choices: bool = True) -> MagicMock:
    """Return a mock openai.OpenAI that yields a response with empty or single choice.

    LocalProvider now uses with_raw_response.create() so we mock that chain.
    The raw response's .parse() returns the ChatCompletion; .json() returns {}.
    """
    mock_response = MagicMock()
    mock_response.choices = [] if empty_choices else [MagicMock()]
    if not empty_choices:
        mock_response.choices[0].message.content = "hello"

    mock_response.model_extra = {}
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


def test_local_provider_raises_on_empty_choices():
    """LocalProvider.complete() should raise ProviderError when choices is empty."""
    from orchid.providers.local import LocalProvider
    from orchid.errors import ProviderError
    import openai as _openai_mod

    p = LocalProvider()
    with patch.object(_openai_mod, "OpenAI", return_value=_mock_openai_client()):
        with pytest.raises(ProviderError, match="empty choices"):
            p.complete([{"role": "user", "content": "hello"}])


def test_ollama_provider_raises_on_empty_choices():
    """OllamaProvider.complete() should raise ProviderError when choices is empty."""
    from orchid.providers.ollama import OllamaProvider
    from orchid.errors import ProviderError
    import openai as _openai_mod

    p = OllamaProvider()
    with patch.object(_openai_mod, "OpenAI", return_value=_mock_openai_client()):
        with pytest.raises(ProviderError, match="empty choices"):
            p.complete([{"role": "user", "content": "hello"}])


def test_openai_provider_raises_on_empty_choices():
    """OpenAIProvider.complete() should raise ProviderError when choices is empty."""
    from orchid.providers.openai import OpenAIProvider
    from orchid.errors import ProviderError
    import openai as _openai_mod

    p = OpenAIProvider(api_key="test-key")
    with patch.object(_openai_mod, "OpenAI", return_value=_mock_openai_client()):
        with pytest.raises(ProviderError, match="empty choices"):
            p.complete([{"role": "user", "content": "hello"}])


# ── 9. Bedrock provider ───────────────────────────────────────────────────────


def test_bedrock_available_with_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    from orchid.providers.bedrock import BedrockProvider
    p = BedrockProvider()
    p.reset_availability_cache()
    assert p.is_available() is True


def test_bedrock_unavailable_without_credentials(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    from orchid.providers.bedrock import BedrockProvider
    p = BedrockProvider()
    p.reset_availability_cache()
    assert p.is_available() is False
    assert "AWS_ACCESS_KEY_ID" in p.fix_suggestion()


def test_bedrock_complete_returns_text(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    from orchid.providers.bedrock import BedrockProvider
    from orchid.errors import ProviderError

    p = BedrockProvider()
    fake_response = {
        "output": {"message": {"content": [{"text": "Hello from Bedrock"}]}}
    }
    mock_client = MagicMock()
    mock_client.converse.return_value = fake_response

    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_client

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        result = p.complete([{"role": "user", "content": "hi"}])

    assert result == "Hello from Bedrock"


def test_bedrock_raises_provider_error_on_bad_response(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    from orchid.providers.bedrock import BedrockProvider
    from orchid.errors import ProviderError

    p = BedrockProvider()
    mock_client = MagicMock()
    mock_client.converse.return_value = {}  # missing keys

    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_client

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        with pytest.raises(ProviderError, match="unexpected response structure"):
            p.complete([{"role": "user", "content": "hi"}])


def test_bedrock_raises_provider_error_when_boto3_missing():
    """Missing boto3 should raise ProviderError, not ImportError."""
    from orchid.providers.bedrock import BedrockProvider
    from orchid.errors import ProviderError
    import sys

    p = BedrockProvider()
    # Temporarily hide boto3 from sys.modules
    original = sys.modules.pop("boto3", None)
    try:
        with pytest.raises(ProviderError, match="boto3"):
            p.complete([{"role": "user", "content": "hi"}])
    finally:
        if original is not None:
            sys.modules["boto3"] = original


# ── 10. models.call() backward compat ─────────────────────────────────────────


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
