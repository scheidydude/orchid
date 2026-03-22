"""Tests for prompt caching support across providers and agents."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── ProviderBase flags ────────────────────────────────────────────────────────


def test_provider_base_explicit_caching_flag_default_false():
    from orchid.providers.base import ProviderBase

    class _Stub(ProviderBase):
        name = "stub"
        def _check_availability(self): return True
        def complete(self, messages, system=None, **kw): return "ok"

    assert _Stub().supports_explicit_caching is False


def test_provider_base_implicit_caching_flag_default_false():
    from orchid.providers.base import ProviderBase

    class _Stub(ProviderBase):
        name = "stub"
        def _check_availability(self): return True
        def complete(self, messages, system=None, **kw): return "ok"

    assert _Stub().supports_implicit_caching is False


def test_anthropic_provider_explicit_caching_flag():
    from orchid.providers.anthropic import AnthropicProvider
    assert AnthropicProvider.supports_explicit_caching is True
    assert AnthropicProvider.supports_implicit_caching is False


def test_local_provider_implicit_caching_flag():
    from orchid.providers.local import LocalProvider
    assert LocalProvider.supports_implicit_caching is True
    assert LocalProvider.supports_explicit_caching is False


def test_ollama_provider_implicit_caching_flag():
    from orchid.providers.ollama import OllamaProvider
    assert OllamaProvider.supports_implicit_caching is True
    assert OllamaProvider.supports_explicit_caching is False


# ── optimize_for_caching ──────────────────────────────────────────────────────


def test_optimize_for_caching_anthropic_returns_blocks():
    from orchid.providers.anthropic import AnthropicProvider, _MIN_CACHE_CHARS
    p = AnthropicProvider()
    stable = "x" * _MIN_CACHE_CHARS
    dynamic = "current user message"
    result = p.optimize_for_caching([stable], [dynamic])
    assert isinstance(result, list)
    # stable block should have cache_control
    assert result[0].get("cache_control") == {"type": "ephemeral"}
    # dynamic block should NOT have cache_control
    assert "cache_control" not in result[-1]


def test_optimize_for_caching_anthropic_small_stable_no_cache():
    from orchid.providers.anthropic import AnthropicProvider
    p = AnthropicProvider()
    # Short stable part — below MIN_CACHE_CHARS, should not get cache_control
    result = p.optimize_for_caching(["short"], ["dynamic"])
    assert "cache_control" not in result[0]


def test_optimize_for_caching_local_returns_string():
    from orchid.providers.local import LocalProvider
    p = LocalProvider()
    result = p.optimize_for_caching(["stable context"], ["dynamic message"])
    assert isinstance(result, str)
    # stable before dynamic
    idx_stable = result.index("stable context")
    idx_dynamic = result.index("dynamic message")
    assert idx_stable < idx_dynamic


def test_optimize_for_caching_ollama_returns_string():
    from orchid.providers.ollama import OllamaProvider
    p = OllamaProvider()
    result = p.optimize_for_caching(["stable"], ["dynamic"])
    assert isinstance(result, str)
    assert "stable" in result
    assert "dynamic" in result


def test_optimize_for_caching_base_default_returns_string():
    from orchid.providers.base import ProviderBase

    class _Stub(ProviderBase):
        name = "stub"
        def _check_availability(self): return True
        def complete(self, messages, system=None, **kw): return "ok"

    result = _Stub().optimize_for_caching(["stable"], ["dynamic"])
    assert isinstance(result, str)
    assert "stable" in result
    assert "dynamic" in result


# ── AnthropicProvider.complete() caching ─────────────────────────────────────


def _mock_anthropic_response(text="hello", cache_write=0, cache_read=0, input_tokens=100):
    """Build a fake Anthropic response."""
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    resp.usage = MagicMock()
    resp.usage.cache_creation_input_tokens = cache_write
    resp.usage.cache_read_input_tokens = cache_read
    resp.usage.input_tokens = input_tokens
    return resp


def _make_fake_anthropic_client(captured: dict, response=None):
    """Return a fake anthropic.Anthropic() instance that captures create() kwargs."""
    if response is None:
        response = _mock_anthropic_response()

    class _FakeMessages:
        @staticmethod
        def create(**kw):
            captured.update(kw)
            return response

    client = MagicMock()
    client.messages = _FakeMessages()
    return client


def test_cache_control_added_to_large_system():
    """Large system prompt is wrapped with cache_control when caching is enabled."""
    from orchid.providers.anthropic import AnthropicProvider, _MIN_CACHE_CHARS

    captured = {}
    fake_client = _make_fake_anthropic_client(captured)

    with patch("anthropic.Anthropic", return_value=fake_client), \
         patch("orchid.config.get", return_value=True):
        p = AnthropicProvider()
        large_system = "You are a helpful assistant. " * 200  # > MIN_CACHE_CHARS
        assert len(large_system) >= _MIN_CACHE_CHARS

        with patch.object(p, "_normalise_messages", return_value=[{"role": "user", "content": "hi"}]):
            p.complete([{"role": "user", "content": "hi"}], system=large_system)

    assert isinstance(captured["system"], list)
    assert captured["system"][0].get("cache_control") == {"type": "ephemeral"}
    assert captured["system"][0]["text"] == large_system


def test_cache_control_not_added_to_small_system():
    """Small system prompt stays as a plain string (no cache_control)."""
    from orchid.providers.anthropic import AnthropicProvider

    captured = {}
    fake_client = _make_fake_anthropic_client(captured)

    with patch("anthropic.Anthropic", return_value=fake_client), \
         patch("orchid.config.get", return_value=True):
        p = AnthropicProvider()
        short_system = "Be helpful."

        with patch.object(p, "_normalise_messages", return_value=[{"role": "user", "content": "hi"}]):
            p.complete([{"role": "user", "content": "hi"}], system=short_system)

    # Short system — should be a plain string, not a list of blocks
    assert isinstance(captured["system"], str)


def test_cacheable_prefix_adds_cache_control_to_messages():
    """cacheable_prefix=1 adds cache_control to the first large message."""
    from orchid.providers.anthropic import AnthropicProvider, _MIN_CACHE_CHARS

    captured = {}
    fake_client = _make_fake_anthropic_client(captured)
    large_content = "A" * _MIN_CACHE_CHARS

    with patch("anthropic.Anthropic", return_value=fake_client), \
         patch("orchid.config.get", return_value=True):
        p = AnthropicProvider()

        with patch.object(
            p, "_normalise_messages",
            return_value=[
                {"role": "user", "content": large_content},
                {"role": "user", "content": "dynamic message"},
            ],
        ):
            p.complete([{"role": "user", "content": large_content}], cacheable_prefix=1)

    msgs = captured["messages"]
    # First message should be converted to content block with cache_control
    first_content = msgs[0]["content"]
    assert isinstance(first_content, list)
    assert first_content[0]["cache_control"] == {"type": "ephemeral"}
    # Second (dynamic) message should remain a plain string
    assert isinstance(msgs[1]["content"], str)


def test_cache_stats_tracked_after_call():
    """Cache stats accumulate from response.usage."""
    from orchid.providers.anthropic import AnthropicProvider, reset_session_stats, get_session_stats

    reset_session_stats()

    captured = {}
    fake_response = _mock_anthropic_response(cache_write=500, cache_read=200, input_tokens=800)
    fake_client = _make_fake_anthropic_client(captured, response=fake_response)

    with patch("anthropic.Anthropic", return_value=fake_client), \
         patch("orchid.config.get", return_value=True):
        p = AnthropicProvider()
        with patch.object(p, "_normalise_messages", return_value=[{"role": "user", "content": "hi"}]):
            p.complete([{"role": "user", "content": "hi"}])

    stats = get_session_stats()
    assert stats["cache_writes"] == 500
    assert stats["cache_hits"] == 200
    assert stats["input_tokens_total"] == 800
    assert stats["input_tokens_cached"] == 200
    assert stats["estimated_savings_pct"] == 25.0


def test_cache_stats_reset():
    """reset_session_stats() zeroes all counters."""
    import orchid.providers.anthropic as ap
    ap._session_stats["cache_writes"] = 99
    ap._session_stats["cache_hits"] = 50

    from orchid.providers.anthropic import reset_session_stats, get_session_stats
    reset_session_stats()
    stats = get_session_stats()
    assert stats["cache_writes"] == 0
    assert stats["cache_hits"] == 0
    assert stats["estimated_savings_pct"] == 0.0


def test_cache_disabled_for_local_provider_no_extra_headers():
    """LocalProvider sends plain string messages (no Anthropic content blocks)."""
    from orchid.providers.local import LocalProvider

    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content="result"))]

    fake_response.model_extra = {}

    with patch("openai.OpenAI") as mock_openai, \
         patch("orchid.config.get", return_value=False):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fake_response
        mock_openai.return_value = mock_client

        p = LocalProvider()
        p.complete([{"role": "user", "content": "hello"}])

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        messages_sent = call_kwargs.get("messages", [])
        for m in messages_sent:
            # Content should always be a plain string for LocalProvider
            assert isinstance(m.get("content", ""), str)


# ── LocalProvider timing extraction ─────────────────────────────────────────


def _make_local_mock_client(timings: dict | None = None, content: str = "result"):
    """Build an OpenAI mock client whose response exposes timings via model_extra."""
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content=content))]
    # model_extra is the Pydantic v2 field the provider reads first
    fake_response.model_extra = {"timings": timings} if timings is not None else {}

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = fake_response
    return mock_client


def test_local_provider_detects_cache_hit_from_timings(tmp_path):
    """Fast prompt eval (high tok/ms) is detected as a cache hit."""
    from orchid.providers.local import LocalProvider
    from orchid.session import Session

    # Create a real session so cache_stats can be updated
    (tmp_path / ".orchid").mkdir()
    (tmp_path / "tasks.md").write_text("# Tasks\n")
    (tmp_path / "CLAUDE.md").write_text("# P\n")
    session = Session(tmp_path)
    session.cache_stats  # ensure exists

    # 500 tokens in 200ms = 0.4 ms/tok → cache hit (< 1.0 ms/tok threshold)
    fast_timings = {"prompt_ms": 200.0, "prompt_n": 500}
    mock_client = _make_local_mock_client(timings=fast_timings)

    with patch("openai.OpenAI", return_value=mock_client), \
         patch("orchid.config.get", return_value=False), \
         patch("orchid.session.get_current_session", return_value=session):
        p = LocalProvider()
        p.complete([{"role": "user", "content": "hello"}])

    assert session.cache_stats["local_fast_evals"] == 1
    assert session.cache_stats["local_slow_evals"] == 0
    assert session.cache_stats["local_prompt_tokens"] == 500
    assert session.cache_stats["local_prompt_ms"] == 200


def test_local_provider_detects_cold_eval_from_timings(tmp_path):
    """Slow prompt eval (low tok/ms) is recorded as a cold eval."""
    from orchid.providers.local import LocalProvider
    from orchid.session import Session

    (tmp_path / ".orchid").mkdir()
    (tmp_path / "tasks.md").write_text("# Tasks\n")
    (tmp_path / "CLAUDE.md").write_text("# P\n")
    session = Session(tmp_path)

    # 100 tokens in 500ms = 5.0 ms/tok → cold eval (> 1.0 ms/tok threshold)
    slow_timings = {"prompt_ms": 500.0, "prompt_n": 100}
    mock_client = _make_local_mock_client(timings=slow_timings)

    with patch("openai.OpenAI", return_value=mock_client), \
         patch("orchid.config.get", return_value=False), \
         patch("orchid.session.get_current_session", return_value=session):
        p = LocalProvider()
        p.complete([{"role": "user", "content": "hello"}])

    assert session.cache_stats["local_slow_evals"] == 1
    assert session.cache_stats["local_fast_evals"] == 0


def test_local_provider_no_timings_no_crash():
    """If timings are absent from the response, complete() still returns content."""
    from orchid.providers.local import LocalProvider

    mock_client = _make_local_mock_client(timings=None)  # no timings key

    with patch("openai.OpenAI", return_value=mock_client), \
         patch("orchid.config.get", return_value=False), \
         patch("orchid.session.get_current_session", return_value=None):
        p = LocalProvider()
        result = p.complete([{"role": "user", "content": "hello"}])

    assert result == "result"


def test_local_provider_response_keys_logged_once(caplog):
    """model_extra keys are logged at DEBUG level on the first call only."""
    import orchid.providers.local as lp
    lp._logged_response_keys = False  # reset flag

    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content="hi"))]
    fake_response.model_extra = {"timings": {"prompt_ms": 5.0, "prompt_n": 10}}
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = fake_response

    import logging
    with patch("openai.OpenAI", return_value=mock_client), \
         patch("orchid.config.get", return_value=False), \
         patch("orchid.session.get_current_session", return_value=None), \
         caplog.at_level(logging.DEBUG, logger="orchid.providers.local"):
        from orchid.providers.local import LocalProvider
        p = LocalProvider()
        p.complete([{"role": "user", "content": "hi"}])

    assert any("[local] response keys:" in r.message for r in caplog.records)
    assert lp._logged_response_keys is True


def test_get_current_session_returns_loaded_session(tmp_path):
    """get_current_session() returns the session set by Session.load()."""
    from orchid.session import get_current_session
    import orchid.session as _sess_mod

    # Reset any existing current session
    _sess_mod._current_session = None
    assert get_current_session() is None

    # Simulate a session being set
    sentinel = object()
    _sess_mod._current_session = sentinel
    assert get_current_session() is sentinel

    _sess_mod._current_session = None  # cleanup


# ── Session metrics ───────────────────────────────────────────────────────────


def test_session_stats_logged_at_session_end(tmp_path):
    """Session.close() includes cache_stats in session_end log event."""
    import orchid.providers.anthropic as ap
    ap._session_stats["cache_writes"] = 10
    ap._session_stats["cache_hits"] = 5
    ap._session_stats["input_tokens_total"] = 1000
    ap._session_stats["input_tokens_cached"] = 500

    # Create a minimal project layout
    (tmp_path / ".orchid").mkdir()
    (tmp_path / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")

    from orchid.session import Session
    s = Session(tmp_path)
    with patch.object(s, "load", return_value=None), \
         patch.object(s, "_maybe_compress_hot_memory", return_value=None), \
         patch.object(s, "save", return_value=None), \
         patch.object(s, "_finalize_live_log", return_value=None), \
         patch.object(s, "_auto_embed_session", return_value=None):
        import json as _json
        events: list[dict] = []
        original_log = s.log_event

        def _capture(event_type, data):
            events.append({"type": event_type, **data})

        s.log_event = _capture
        s.close(summary="test session")

    session_end = next((e for e in events if e.get("type") == "session_end"), None)
    assert session_end is not None
    cache = session_end.get("cache_stats", {})
    assert cache.get("cache_writes") == 10
    assert cache.get("cache_hits") == 5
