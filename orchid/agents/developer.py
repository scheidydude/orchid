"""Developer agent — code-focused, runs on local model by default."""

from __future__ import annotations

from orchid.agents.base import BaseAgent


class DeveloperAgent(BaseAgent):
    """Writes, edits, and debugs code."""

    model_key = "local"

    def system_prompt(self) -> str:
        base = super().system_prompt()
        return (
            "You are an expert software engineer. Your role is to write, edit, and debug code.\n"
            "Always write clean, idiomatic Python 3.12. Use type hints. No unnecessary comments.\n\n"
        ) + base
