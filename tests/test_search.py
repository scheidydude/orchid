"""Tests for tools/search.py — WebSearchTool, backends, page fetching."""

from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock, patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reset():
    from orchid.tools.search import reset_backend_cache
    reset_backend_cache()


# ── Backend availability tests ────────────────────────────────────────────────

def test_searxng_backend_skipped_when_unavailable():
    from orchid.tools.search import SearXNGBackend
    b = SearXNGBackend("http://localhost:19999")  # nothing listening there
    assert not b.available()


def test_brave_backend_skipped_when_no_key():
    from orchid.tools.search import BraveBackend
    b = BraveBackend("")
    assert not b.available()


def test_brave_backend_available_with_key():
    from orchid.tools.search import BraveBackend
    b = BraveBackend("fake_key_xyz")
    assert b.available()


def test_duckduckgo_always_available():
    from orchid.tools.search import DuckDuckGoBackend
    assert DuckDuckGoBackend().available()


# ── Backend auto-selection ────────────────────────────────────────────────────

def test_backend_auto_selection_falls_to_ddg(monkeypatch):
    """With no SearXNG and no Brave key, auto should select DuckDuckGo."""
    _reset()
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    # Patch SearXNG to be unavailable
    with patch("orchid.tools.search.SearXNGBackend.available", return_value=False):
        from orchid.tools.search import _detect_backend
        backend = _detect_backend()
        assert backend is not None
        assert backend.name == "duckduckgo"
    _reset()


def test_backend_auto_selection_prefers_searxng(monkeypatch):
    """When SearXNG is available it should be chosen over DDG."""
    _reset()
    with patch("orchid.tools.search.SearXNGBackend.available", return_value=True):
        from orchid.tools.search import _detect_backend
        backend = _detect_backend()
        assert backend is not None
        assert backend.name == "searxng"
    _reset()


def test_backend_auto_selection_uses_brave_when_searxng_down(monkeypatch):
    """Brave should be chosen when SearXNG is down and BRAVE_API_KEY is set."""
    _reset()
    monkeypatch.setenv("BRAVE_API_KEY", "test_brave_key")
    with patch("orchid.tools.search.SearXNGBackend.available", return_value=False):
        from orchid.tools.search import _detect_backend
        backend = _detect_backend()
        assert backend is not None
        assert backend.name == "brave"
    _reset()


def test_search_returns_error_when_no_backend(monkeypatch):
    """With no backend at all, search should return an error result, not crash."""
    _reset()
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    with patch("orchid.tools.search.SearXNGBackend.available", return_value=False), \
         patch("orchid.tools.search.DuckDuckGoBackend.available", return_value=False):
        from orchid.tools.search import WebSearchTool
        tool = WebSearchTool()
        results = tool.search("test query")
        assert len(results) == 1
        assert "No web search backend available" in results[0]["snippet"]
    _reset()


def test_search_falls_back_to_ddg_when_searxng_fails_mid_query(monkeypatch):
    """If SearXNG is available but raises during search(), DDG should be tried."""
    _reset()
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    fake_results = [{"title": "DDG result", "url": "http://example.com", "snippet": "from ddg", "source": "duckduckgo"}]

    with patch("orchid.tools.search.SearXNGBackend.available", return_value=True), \
         patch("orchid.tools.search.SearXNGBackend.search", side_effect=ConnectionError("timeout")), \
         patch("orchid.tools.search.DuckDuckGoBackend.available", return_value=True), \
         patch("orchid.tools.search.DuckDuckGoBackend.search", return_value=fake_results):
        from orchid.tools.search import WebSearchTool
        tool = WebSearchTool()
        results = tool.search("test query")

    assert len(results) == 1
    assert results[0]["source"] == "duckduckgo"
    _reset()


def test_search_returns_error_when_all_backends_fail(monkeypatch):
    """If every backend raises during search(), return an error dict."""
    _reset()
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    with patch("orchid.tools.search.SearXNGBackend.available", return_value=True), \
         patch("orchid.tools.search.SearXNGBackend.search", side_effect=ConnectionError("timeout")), \
         patch("orchid.tools.search.DuckDuckGoBackend.available", return_value=True), \
         patch("orchid.tools.search.DuckDuckGoBackend.search", side_effect=RuntimeError("ddg down")):
        from orchid.tools.search import WebSearchTool
        tool = WebSearchTool()
        results = tool.search("test query")

    assert len(results) == 1
    assert "All search backends failed" in results[0]["snippet"]
    _reset()


def test_searxng_default_url_is_scheidy():
    """Default SearXNG URL should be searxng.scheidy.com, not localhost."""
    from orchid.tools.search import _build_candidates
    candidates = _build_candidates()
    searxng = next(c for c in candidates if c.name == "searxng")
    assert "scheidy.com" in searxng.base_url or "SEARXNG_URL" in str(searxng.base_url)


# ── DuckDuckGo live test ──────────────────────────────────────────────────────

@pytest.mark.network
def test_duckduckgo_backend_returns_results():
    """Real network test — DuckDuckGo HTML scraping. Mark skip if offline."""
    _reset()
    from orchid.tools.search import DuckDuckGoBackend
    b = DuckDuckGoBackend()
    results = b.search("Python programming language", n=3)
    assert len(results) >= 1
    assert all("title" in r and "url" in r for r in results)
    # At least one result should have a non-empty URL
    assert any(r["url"] for r in results)


# ── Page fetch tests ──────────────────────────────────────────────────────────

def test_fetch_page_extracts_content():
    """fetch_page() should extract meaningful text from HTML."""
    html = """
    <html><head><title>Test</title></head>
    <body>
      <nav>Navigation stuff</nav>
      <article>
        <h1>Main Article Title</h1>
        <p>This is the main content of the article with useful information.</p>
        <p>Second paragraph with more useful content about the topic.</p>
      </article>
      <footer>Footer stuff</footer>
    </body></html>
    """
    import httpx
    from unittest.mock import patch, MagicMock

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_resp):
        from orchid.tools.search import fetch_page
        content = fetch_page("http://example.com/article")

    assert len(content) > 0
    assert "[fetch error" not in content
    # Should contain article text
    assert "useful" in content.lower() or "article" in content.lower()


def test_fetch_page_handles_network_error():
    """fetch_page() should return an error string, not raise, on network failure."""
    import httpx
    with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
        from orchid.tools.search import fetch_page
        result = fetch_page("http://localhost:19999/nothing")
    assert "[fetch error" in result


def test_fetch_page_truncates_long_content():
    """fetch_page() should truncate to max_page_chars."""
    # Build a big HTML page
    big_text = " ".join(["word"] * 10000)
    html = f"<html><body><article><p>{big_text}</p></article></body></html>"

    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_resp):
        from orchid.tools.search import fetch_page
        content = fetch_page("http://example.com/big")

    assert len(content) <= 8000 + 100  # small tolerance for truncation boundary


# ── Vector embedding on search ────────────────────────────────────────────────

def test_search_result_embedded_into_vector_store(tmp_path):
    """After search(), results should be present in the vector store."""
    _reset()

    # Mock embed to avoid real network call
    def _fake_embed(text):
        import math
        seed = sum(ord(c) for c in text[:64])
        vals = [math.sin(seed * (i + 1)) for i in range(8)]
        norm = math.sqrt(sum(v * v for v in vals)) or 1.0
        return [v / norm for v in vals]

    # Fake search results from DDG (no real network)
    fake_results = [
        {"title": "Orchid framework overview", "url": "http://example.com/orchid", "snippet": "AI agent orchestration", "source": "duckduckgo"},
        {"title": "Agent design patterns", "url": "http://example.com/agents", "snippet": "ReAct loop design", "source": "duckduckgo"},
    ]

    with patch("orchid.tools.models.embed", side_effect=_fake_embed), \
         patch("orchid.tools.search.DuckDuckGoBackend.search", return_value=fake_results), \
         patch("orchid.tools.search.SearXNGBackend.available", return_value=False):

        from orchid.memory.vector import VectorMemory
        vm = VectorMemory(project_dir=tmp_path)

        tool = WebSearchTool(vector_memory=vm, project_name="test")
        results = tool.search("AI agent orchestration", n=2)

    assert len(results) == 2
    # Results should now be in vector store
    with patch("orchid.tools.models.embed", side_effect=_fake_embed):
        recall_results = vm.query("AI agent orchestration", n=3)
    assert len(recall_results) > 0
    assert any(r["metadata"].get("type") == "research" for r in recall_results)

    _reset()


# Import here for the last test
from orchid.tools.search import WebSearchTool


# ── ReAct bracket action parsing ──────────────────────────────────────────────

def test_react_bracket_search_parsed():
    """Action: search[query] shorthand should be parsed correctly."""
    from orchid.agents.base import _ACTION_BRACKET_RE
    response = "Thought: I need to search.\nAction: search[Python asyncio tutorial]"
    m = _ACTION_BRACKET_RE.search(response)
    assert m is not None
    assert m.group(1) == "search"
    assert m.group(2) == "Python asyncio tutorial"


def test_react_bracket_fetch_parsed():
    """Action: fetch[url] shorthand should be parsed correctly."""
    from orchid.agents.base import _ACTION_BRACKET_RE
    response = "Thought: Let me read this page.\nAction: fetch[https://example.com/page]"
    m = _ACTION_BRACKET_RE.search(response)
    assert m is not None
    assert m.group(1) == "fetch"
    assert m.group(2) == "https://example.com/page"


def test_react_json_action_still_works():
    """Original JSON Action format must still parse."""
    from orchid.agents.base import _ACTION_RE
    response = 'Thought: check files\nAction: read_file\nAction Input: {"path": "tasks.md"}'
    m = _ACTION_RE.search(response)
    assert m is not None
    assert m.group(1) == "read_file"
