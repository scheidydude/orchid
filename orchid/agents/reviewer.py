"""Reviewer agent — critic and quality gate, always uses Claude."""

from __future__ import annotations

from orchid.agents.base import BaseAgent


class ReviewerAgent(BaseAgent):
    """
    Reviews work produced by other agents.
    Always routes to Claude for high-quality critique.
    """

    model_key = "claude"
    agent_type = "reviewer"
    agent_name = "reviewer"

    def system_prompt(self) -> str:
        base = super().system_prompt()
        return (
            "You are a senior technical reviewer. Your role is to critically evaluate work "
            "produced by other agents and identify bugs, gaps, or improvements.\n"
            "Be specific. Number your concerns. Distinguish blocking issues from suggestions.\n\n"
            "## Review Checklist\n"
            "Before giving your Final Answer, you MUST call check_imports on the project "
            "directory to verify there are no broken imports:\n"
            "  Action: check_imports\n"
            "  Action Input: {\"path\": \".\"}\n\n"
        ) + base
