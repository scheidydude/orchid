"""Researcher agent — web search, page fetching, summarization."""

from __future__ import annotations

from orchid.agents.base import _BUILTIN_SCHEMAS, _SEARCH_SCHEMAS, BaseAgent


class ResearcherAgent(BaseAgent):
    """Searches the web, reads pages, and synthesizes information."""

    model_key = "local"
    agent_type = "researcher"
    agent_name = "researcher"

    # search/fetch added by __init__ after super(); they bypass the filter by design
    allowed_tools: frozenset[str] | None = frozenset({
        "read_file", "list_dir", "bash", "search", "fetch", "get_task_files",
    })

    def __init__(self, *args, vector_memory=None, project_name: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        from orchid.tools.search import WebSearchTool  # noqa: PLC0415
        self._search_tool = WebSearchTool(
            vector_memory=vector_memory,
            project_name=project_name,
        )
        # Register search and fetch as dispatchable tools
        self.register_tool("search", self._do_search)
        self.register_tool("fetch", self._do_fetch)

    def _do_search(self, query: str) -> str:
        return self._search_tool.search_and_format(query)

    def _do_fetch(self, url: str) -> str:
        return self._search_tool.fetch_page(url)

    def system_prompt(self) -> str:
        search_tool_list = "\n".join(
            f"- {s['name']}: {s['description']}" for s in _SEARCH_SCHEMAS
        )
        base_tool_list = "\n".join(
            f"- {s['name']}: {s['description']}" for s in _BUILTIN_SCHEMAS
        )
        return (
            "You are a research assistant inside the Orchid orchestration framework.\n"
            "Your role: find, read, and synthesize information from the web and local files.\n"
            "Be concise. Cite URLs when available. Summarize key findings clearly.\n\n"
            "## Web Search Tools\n"
            f"{search_tool_list}\n\n"
            "You may use either format:\n"
            "  Action: search[your query here]\n"
            "  — or —\n"
            "  Action: search\n"
            "  Action Input: {\"query\": \"your query here\"}\n\n"
            "## File & Shell Tools\n"
            f"{base_tool_list}\n\n"
            "## ReAct Format\n"
            "Think step by step. Use tools to gather information, then:\n"
            "Final Answer: <concise synthesis with sources>\n\n"
            "## Project Context\n"
            f"{self.session_context}"
        )
