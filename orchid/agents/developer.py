"""Developer agent — code-focused, runs on local model by default."""

from __future__ import annotations

from orchid.agents.base import BaseAgent


class DeveloperAgent(BaseAgent):
    """Writes, edits, and debugs code."""

    model_key = "local"
    agent_type = "developer"

    def system_prompt(self) -> str:
        base = super().system_prompt()
        return (
            "You are an expert software engineer. Your role is to write, edit, and debug code.\n"
            "Always write clean, idiomatic Python 3.12. Use type hints. No unnecessary comments.\n\n"
            "## Delegation Guidelines\n"
            "- For research-first tasks (e.g., researching libraries, APIs, best practices, or external information),\n"
            "  use the delegate action to spawn a researcher agent instead of searching yourself.\n"
            "- Example: Action: delegate[researcher | Research the best Python library for PDF parsing]\n"
            "- Only use direct tools (read_file, bash, etc.) for implementation tasks.\n\n"
        ) + base
