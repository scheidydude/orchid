"""Reviewer agent — critic and quality gate, always uses Claude."""

from __future__ import annotations

from orchid.agents.base import BaseAgent


class ReviewerAgent(BaseAgent):
    """
    Reviews work produced by other agents.
    Always routes to Claude for high-quality critique.
    """

    model_key = "claude"

    def system_prompt(self) -> str:
        base = super().system_prompt()
        return (
            "You are a senior technical reviewer. Your role is to critically evaluate work "
            "produced by other agents and identify bugs, gaps, or improvements.\n"
            "Be specific. Number your concerns. Distinguish blocking issues from suggestions.\n\n"
        ) + base
