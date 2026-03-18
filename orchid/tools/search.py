"""Web search tool — SearXNG / Brave / DuckDuckGo with trafilatura page extraction."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from orchid import config as cfg

logger = logging.getLogger(__name__)

# ── Result type ───────────────────────────────────────────────────────────────

SearchResult = dict[str, str]  # {title, url, snippet, source}

# ── Backend availability cache ────────────────────────────────────────────────

_active_backend: "_Backend | None | str" = "unprobed"  # sentinel
_active_backend_ts: float = 0.0


# ── Backend base ──────────────────────────────────────────────────────────────

class _Backend:
    name: str = "base"

    def available(self) -> bool:
        raise NotImplementedError

    def search(self, query: str, n: int) -> list[SearchResult]:
        raise NotImplementedError


# ── SearXNG ───────────────────────────────────────────────────────────────────

class SearXNGBackend(_Backend):
    name = "searxng"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def available(self) -> bool:
        import httpx  # noqa: PLC0415
        try:
            r = httpx.get(f"{self.base_url}/", timeout=3.0)
            return r.status_code < 500
        except Exception:
            return False

    def search(self, query: str, n: int) -> list[SearchResult]:
        import httpx  # noqa: PLC0415
        timeout = cfg.get("web_search.timeout_seconds", 10)
        resp = httpx.post(
            f"{self.base_url}/search",
            data={"q": query, "format": "json", "categories": "general"},
            timeout=float(timeout),
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[SearchResult] = []
        for item in data.get("results", [])[:n]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "source": "searxng",
            })
        return results


# ── Brave ─────────────────────────────────────────────────────────────────────

class BraveBackend(_Backend):
    name = "brave"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, n: int) -> list[SearchResult]:
        import httpx  # noqa: PLC0415
        timeout = cfg.get("web_search.timeout_seconds", 10)
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": n},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": self.api_key,
            },
            timeout=float(timeout),
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[SearchResult] = []
        for item in data.get("web", {}).get("results", [])[:n]:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", ""),
                "source": "brave",
            })
        return results


# ── DuckDuckGo ────────────────────────────────────────────────────────────────

class DuckDuckGoBackend(_Backend):
    name = "duckduckgo"

    def available(self) -> bool:
        return True  # always available (no API key required)

    def search(self, query: str, n: int) -> list[SearchResult]:
        import httpx  # noqa: PLC0415
        from bs4 import BeautifulSoup  # noqa: PLC0415

        timeout = cfg.get("web_search.timeout_seconds", 10)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

        # DuckDuckGo HTML endpoint
        resp = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=headers,
            timeout=float(timeout),
            follow_redirects=True,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        results: list[SearchResult] = []

        for result_div in soup.select(".result__body")[:n]:
            title_el = result_div.select_one(".result__title a")
            snippet_el = result_div.select_one(".result__snippet")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")
            # DDG wraps URLs in redirects — extract uddg= param when present
            if "uddg=" in url:
                from urllib.parse import urlparse, parse_qs, unquote  # noqa: PLC0415
                qs = parse_qs(urlparse(url).query)
                url = unquote(qs.get("uddg", [url])[0])
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": "duckduckgo",
            })

        return results


# ── Backend selection ─────────────────────────────────────────────────────────

def _detect_backend() -> _Backend | None:
    global _active_backend, _active_backend_ts  # noqa: PLW0603

    ttl = cfg.get("web_search.backend_cache_ttl", 300)
    if _active_backend != "unprobed" and (time.time() - _active_backend_ts) < ttl:
        return _active_backend  # type: ignore[return-value]

    mode = cfg.get("web_search.backend", "auto")

    if mode == "searxng":
        candidates: list[_Backend] = [
            SearXNGBackend(cfg.get("web_search.searxng_url", os.environ.get("SEARXNG_URL", "http://localhost:8888")))
        ]
    elif mode == "brave":
        candidates = [BraveBackend(os.environ.get("BRAVE_API_KEY", ""))]
    elif mode == "duckduckgo":
        candidates = [DuckDuckGoBackend()]
    else:  # auto
        searxng_url = os.environ.get("SEARXNG_URL", cfg.get("web_search.searxng_url", "http://localhost:8888"))
        brave_key = os.environ.get("BRAVE_API_KEY", "")
        candidates = [
            SearXNGBackend(searxng_url),
            BraveBackend(brave_key),
            DuckDuckGoBackend(),
        ]

    for backend in candidates:
        if backend.available():
            logger.info("Web search backend: %s", backend.name)
            _active_backend = backend
            _active_backend_ts = time.time()
            return backend

    logger.warning("No web search backend available.")
    _active_backend = None
    _active_backend_ts = time.time()
    return None


def reset_backend_cache() -> None:
    """Reset cached backend — useful in tests."""
    global _active_backend, _active_backend_ts  # noqa: PLW0603
    _active_backend = "unprobed"
    _active_backend_ts = 0.0


# ── Page fetcher ──────────────────────────────────────────────────────────────

def fetch_page(url: str) -> str:
    """
    Fetch a URL and extract main text content via trafilatura.

    Returns plain text, truncated to web_search.max_page_chars (default 8000).
    """
    import httpx  # noqa: PLC0415
    import trafilatura  # noqa: PLC0415

    timeout = cfg.get("web_search.timeout_seconds", 10)
    max_chars = cfg.get("web_search.max_page_chars", 8000)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        resp = httpx.get(url, headers=headers, timeout=float(timeout), follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        return f"[fetch error: {exc}]"

    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not text:
        # Fallback: strip all tags
        from bs4 import BeautifulSoup  # noqa: PLC0415
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ", strip=True)

    if not text:
        return "[no content extracted]"

    return text[:max_chars]


# ── WebSearchTool ─────────────────────────────────────────────────────────────

class WebSearchTool:
    """
    Unified web search interface used by agents.

    Wraps backend auto-selection, result formatting, and optional vector embedding.
    """

    def __init__(
        self,
        vector_memory: Any = None,
        project_name: str = "",
    ) -> None:
        self._vector = vector_memory
        self._project_name = project_name

    def search(self, query: str, n: int | None = None) -> list[SearchResult]:
        """Search the web. Returns list of {title, url, snippet, source}."""
        if not cfg.get("web_search.enabled", True):
            return [{"title": "", "url": "", "snippet": "Web search disabled in config.", "source": ""}]

        n_results = n or cfg.get("web_search.max_results", 5)
        backend = _detect_backend()
        if backend is None:
            return [{"title": "error", "url": "", "snippet": "No web search backend available.", "source": ""}]

        try:
            results = backend.search(query, n_results)
        except Exception as exc:
            logger.warning("Search failed (%s): %s", backend.name, exc)
            return [{"title": "error", "url": "", "snippet": f"Search error: {exc}", "source": backend.name}]

        if cfg.get("web_search.embed_results", True) and self._vector and self._vector.available:
            self._embed_results(query, results)

        return results

    def fetch_page(self, url: str) -> str:
        """Fetch and extract main content from a URL."""
        return fetch_page(url)

    def _embed_results(self, query: str, results: list[SearchResult]) -> None:
        for r in results:
            text = f"{r['title']}\n{r['snippet']}"
            if not text.strip():
                continue
            try:
                self._vector.add(
                    text,
                    metadata={
                        "type": "research",
                        "query": query[:200],
                        "url": r.get("url", ""),
                        "project": self._project_name,
                        "source": r.get("source", ""),
                    },
                )
            except Exception as exc:
                logger.debug("Failed to embed search result: %s", exc)

    def search_and_format(self, query: str, n: int | None = None) -> str:
        """Convenience: search and return a human-readable string for agent prompts."""
        results = self.search(query, n)
        if not results:
            return "No results found."
        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r['title']}")
            if r["url"]:
                lines.append(f"    URL: {r['url']}")
            if r["snippet"]:
                lines.append(f"    {r['snippet']}")
            lines.append("")
        return "\n".join(lines)
