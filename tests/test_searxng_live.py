"""Live connectivity tests for the SearXNG instance at searxng.scheidy.com.

Run with:  pytest tests/test_searxng_live.py -v
These tests make real network calls — they are excluded from the default
suite (marked 'network') so CI doesn't depend on an external service.
"""

from __future__ import annotations

import pytest
from orchid.tools.search import SearXNGBackend, reset_backend_cache, _detect_backend


SEARXNG_URL = "https://searxng.scheidy.com"


@pytest.fixture(autouse=True)
def _reset():
    reset_backend_cache()
    yield
    reset_backend_cache()


@pytest.mark.network
def test_searxng_healthz_responds():
    """Health endpoint should return 200."""
    import httpx
    r = httpx.get(f"{SEARXNG_URL}/healthz", timeout=5.0)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


@pytest.mark.network
def test_searxng_backend_reports_available():
    """SearXNGBackend.available() should return True."""
    b = SearXNGBackend(SEARXNG_URL)
    assert b.available(), f"SearXNG at {SEARXNG_URL} reported unavailable"


@pytest.mark.network
def test_orchid_selects_searxng_as_primary_backend():
    """_detect_backend() should select SearXNG when it is reachable."""
    backend = _detect_backend()
    assert backend is not None, "No backend selected"
    assert backend.name == "searxng", (
        f"Expected searxng but got '{backend.name}' — "
        f"check that {SEARXNG_URL} is reachable"
    )


@pytest.mark.network
def test_searxng_returns_results():
    """A real search should return at least one result with title and URL."""
    b = SearXNGBackend(SEARXNG_URL)
    results = b.search("Python programming language", n=3)
    assert len(results) >= 1, "No results returned"
    assert all("title" in r and "url" in r for r in results)
    assert any(r["url"].startswith("http") for r in results)


@pytest.mark.network
def test_searxng_result_source_tagged_correctly():
    """Results should carry source='searxng'."""
    b = SearXNGBackend(SEARXNG_URL)
    results = b.search("orchid AI agent", n=2)
    assert all(r.get("source") == "searxng" for r in results)


@pytest.mark.network
def test_websearchtool_uses_searxng_end_to_end():
    """WebSearchTool.search() should return results via SearXNG."""
    from orchid.tools.search import WebSearchTool
    tool = WebSearchTool()
    results = tool.search("site reliability engineering", n=3)
    assert len(results) >= 1
    assert results[0].get("source") == "searxng", (
        f"Expected source=searxng, got {results[0].get('source')}"
    )
