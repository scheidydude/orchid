"""SearXNG MCP server — runs as a stdio subprocess."""
from __future__ import annotations

import asyncio
import html.parser

import httpx
import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

SEARXNG_BASE = "https://search.scheidy.com"
SEARCH_TIMEOUT = 10.0
FETCH_TIMEOUT = 15.0
MAX_RESULTS = 8
MAX_FETCH_CHARS = 12_000

app = Server("searxng")


class _TextStripper(html.parser.HTMLParser):
    """Minimal HTML → plain text stripper using stdlib only."""

    SKIP_TAGS = {"script", "style", "head", "nav", "footer", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def get_text(self) -> str:
        return "\n".join(self._parts)


def _strip_html(raw: str) -> str:
    parser = _TextStripper()
    parser.feed(raw)
    return parser.get_text()


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="web_search",
            description=(
                "Search the web via SearXNG. Returns titles, URLs, and snippets "
                "for the top results. Use for general research queries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {
                        "type": "integer",
                        "description": "Max results to return (default 5, max 8)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="fetch_page",
            description=(
                "Fetch and return the text content of a web page. "
                "Use to read a specific URL in full after finding it via web_search."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                },
                "required": ["url"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    if name == "web_search":
        return await _web_search(
            query=arguments["query"],
            max_results=min(int(arguments.get("max_results", 5)), MAX_RESULTS),
        )
    if name == "fetch_page":
        return await _fetch_page(url=arguments["url"])
    raise ValueError(f"Unknown tool: {name}")


async def _web_search(query: str, max_results: int) -> list[types.TextContent]:
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
        resp = await client.get(
            f"{SEARXNG_BASE}/search",
            params={"q": query, "format": "json"},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])[:max_results]
    if not results:
        return [types.TextContent(type="text", text="No results found.")]

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', 'No title')}")
        lines.append(f"   URL: {r.get('url', '')}")
        snippet = r.get("content", "").strip()
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _fetch_page(url: str) -> list[types.TextContent]:
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (research-agent/1.0)"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "html" in content_type:
            text = _strip_html(resp.text)
        else:
            text = resp.text

    text = text[:MAX_FETCH_CHARS]
    if len(resp.text) > MAX_FETCH_CHARS:
        text += f"\n\n[truncated — {len(resp.text)} total chars]"

    return [types.TextContent(type="text", text=f"Content of {url}:\n\n{text}")]


async def _run() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
