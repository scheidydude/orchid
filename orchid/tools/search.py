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
# Full ordered chain of available backends, rebuilt when TTL expires.
# Storing the chain (not just the first) enables per-query fallback: if the
# primary backend fails during a search call, WebSearchTool tries the next.

_backend_chain: "list[_Backend]" = []
_backend_chain_ts: float = 0.0


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
            # Prefer /healthz (SearXNG ≥ 2023.x); fall back to / for older installs
            r = httpx.get(f"{self.base_url}/healthz", timeout=3.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
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

def _build_candidates() -> list[_Backend]:
    """Return the ordered list of backends to probe, based on config/mode."""
    mode = cfg.get("web_search.backend", "auto")
    searxng_url = cfg.get(
        "web_search.searxng_url",
        os.environ.get("SEARXNG_URL", "https://searxng.scheidy.com"),
    )
    brave_key = os.environ.get("BRAVE_API_KEY", "")

    if mode == "searxng":
        return [SearXNGBackend(searxng_url)]
    if mode == "brave":
        return [BraveBackend(brave_key)]
    if mode == "duckduckgo":
        return [DuckDuckGoBackend()]
    # auto: SearXNG → Brave → DuckDuckGo
    return [SearXNGBackend(searxng_url), BraveBackend(brave_key), DuckDuckGoBackend()]


def _get_backend_chain() -> list[_Backend]:
    """Return (and cache) the ordered list of currently-available backends."""
    global _backend_chain, _backend_chain_ts  # noqa: PLW0603

    ttl = cfg.get("web_search.backend_cache_ttl", 300)
    if _backend_chain and (time.time() - _backend_chain_ts) < ttl:
        return _backend_chain

    chain = [b for b in _build_candidates() if b.available()]
    if chain:
        logger.info("Web search chain: %s", [b.name for b in chain])
    else:
        logger.warning("No web search backend available.")
    _backend_chain = chain
    _backend_chain_ts = time.time()
    return chain


def _detect_backend() -> _Backend | None:
    """Return the highest-priority available backend (backward-compat helper)."""
    chain = _get_backend_chain()
    return chain[0] if chain else None


def reset_backend_cache() -> None:
    """Reset cached backend chain — forces re-probe on next search. Useful in tests."""
    global _backend_chain, _backend_chain_ts  # noqa: PLW0603
    _backend_chain = []
    _backend_chain_ts = 0.0


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
        """Search the web. Returns list of {title, url, snippet, source}.

        Tries each backend in priority order (SearXNG → Brave → DDG).
        If a backend raises during the search call it is skipped and the
        next one is tried, so a SearXNG outage is transparent to callers.
        """
        if not cfg.get("web_search.enabled", True):
            return [{"title": "", "url": "", "snippet": "Web search disabled in config.", "source": ""}]

        n_results = n or cfg.get("web_search.max_results", 5)
        chain = _get_backend_chain()
        if not chain:
            return [{"title": "error", "url": "", "snippet": "No web search backend available.", "source": ""}]

        last_exc: Exception | None = None
        for backend in chain:
            try:
                results = backend.search(query, n_results)
                if cfg.get("web_search.embed_results", True) and self._vector and self._vector.available:
                    self._embed_results(query, results)
                return results
            except Exception as exc:
                logger.warning("Search backend %s failed: %s — trying next", backend.name, exc)
                last_exc = exc
                # Invalidate chain so next search re-probes availability
                reset_backend_cache()

        return [{"title": "error", "url": "", "snippet": f"All search backends failed: {last_exc}", "source": ""}]

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
