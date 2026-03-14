"""Researcher agent — search, summarize, and gather information."""

from __future__ import annotations

from orchid.agents.base import BaseAgent


class ResearcherAgent(BaseAgent):
    """Searches, reads, and summarizes information."""

    model_key = "local"

    def system_prompt(self) -> str:
        base = super().system_prompt()
        return (
            "You are a research assistant. Your role is to find, read, and synthesize information.\n"
            "Be concise. Cite sources when available. Summarize key findings clearly.\n\n"
        ) + base
