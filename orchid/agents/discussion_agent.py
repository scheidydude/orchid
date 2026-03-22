"""DiscussionAgent — conversational requirements capture for Orchid V2.

Uses the provider registry (default: claude) for a structured conversation
to elicit project requirements. Each turn returns a DiscussionResponse with
the agent's reply, a readiness signal, context updates, and suggested follow-ups.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DiscussionResponse:
    message: str                          # agent's reply to show the user
    ready_to_advance: bool = False        # agent thinks requirements are clear
    context_updates: str = ""            # new decisions/constraints to capture
    suggestions: list[str] = field(default_factory=list)  # follow-up prompts


_SYSTEM_INSTRUCTIONS = """\
You are a senior software architect and product manager helping a developer define
requirements for a new project. Your role is to have a friendly, focused conversation
to capture what the developer wants to build.

## Guidelines
- Ask at most 3 clarifying questions per turn.
- Be concrete — prefer specific technology choices over vague descriptions.
- Capture decisions as you go; don't re-ask things already answered.
- When you have enough information to write a solid requirements doc, signal readiness.

## Response Format
Always respond using EXACTLY this structure (XML-style markers, no deviation):

<reply>
Your conversational reply to the developer. Ask clarifying questions here.
</reply>

<context_updates>
Any new decisions or constraints captured this turn (or empty if none).
Format as bullet points. Example:
- Backend: FastAPI (confirmed)
- Auth: JWT tokens
</context_updates>

<ready>false</ready>

Replace <ready>false</ready> with <ready>true</ready> ONLY when you have enough
information to produce a complete REQUIREMENTS.md and ARCHITECTURE.md without
further questions.

<suggestions>
- Optional follow-up prompt the user could ask
- Another optional suggestion
</suggestions>

The <suggestions> block is optional. Use it to help the user explore important
topics they may have overlooked.
"""


def _extract_tag(text: str, tag: str) -> str:
    """Extract content between <tag> and </tag>; return '' if not found."""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_suggestions(text: str) -> list[str]:
    raw = _extract_tag(text, "suggestions")
    if not raw:
        return []
    lines = [
        ln.lstrip("-• ").strip()
        for ln in raw.splitlines()
        if ln.strip() and ln.strip() not in ("-", "•")
    ]
    return [ln for ln in lines if ln]


class DiscussionAgent:
    """Conversational agent for V2 requirements elicitation.

    Does NOT use the ReAct loop — this is a direct back-and-forth chat
    using the provider registry.
    """

    agent_type = "discussion"

    def __init__(
        self,
        project_dir: Path,
        cli_override: str | None = None,
        offline: bool = False,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self._cli_override = cli_override
        self._offline = offline

    def _get_provider(self):
        from orchid import config as cfg
        from orchid.providers.registry import get_registry, reset_registry

        cfg.configure_for_project(self.project_dir)
        registry = get_registry()
        if self._offline:
            registry.set_offline(True)
        return registry.resolve("discussion", cli_override=self._cli_override)

    def run(
        self,
        user_message: str,
        history: "DiscussionHistory",  # noqa: F821  (forward ref OK at runtime)
        machine_profile=None,
    ) -> DiscussionResponse:
        """Process one user turn and return a DiscussionResponse."""
        from orchid.discussion import DiscussionHistory
        from orchid.machine_profile import MachineProfile

        if machine_profile is None:
            machine_profile = MachineProfile.load()

        machine_context = machine_profile.to_context_string()
        context_md = history.get_context_md()
        discussion_history = history.to_prompt_context()

        # System: static instructions only — stable across all turns, auto-cached
        system = _SYSTEM_INSTRUCTIONS

        provider = self._get_provider()

        # Warn if using local model for planning
        from orchid import config as cfg
        warn = cfg.get("lifecycle.warn_on_local_planning", True)
        if warn and getattr(provider, "provider_type", "") not in ("anthropic",):
            logger.warning(
                "Using local model for discussion agent — output quality may differ. "
                "Use --provider discussion=claude for best results."
            )

        # Build message content using provider's caching strategy:
        #   stable: machine context + context summary + prior conversation (cacheable)
        #   dynamic: current user message (changes every turn)
        stable_context = "\n\n".join(filter(None, [
            f"## Developer's Environment\n{machine_context}" if machine_context else "",
            f"## Current Context Summary\n{context_md}" if context_md else "",
            f"## Conversation History\n{discussion_history}" if discussion_history else "## Conversation History\n(no conversation yet)",
        ]))
        content = provider.optimize_for_caching(
            stable_parts=[stable_context],
            dynamic_parts=[f"Developer: {user_message}"],
        )
        messages = [{"role": "user", "content": content}]

        raw = provider.complete(messages, system=system)
        return self._parse_response(raw)

    def update_context(
        self, history: "DiscussionHistory", context_updates: str  # noqa: F821
    ) -> None:
        """Persist context updates to context.md."""
        if context_updates:
            history.update_context(context_updates)

    # ── Parsing ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_response(raw: str) -> DiscussionResponse:
        reply = _extract_tag(raw, "reply")
        if not reply:
            # Graceful fallback: treat whole response as reply
            reply = raw.strip()

        ready_str = _extract_tag(raw, "ready").lower()
        ready = ready_str == "true"

        context_updates = _extract_tag(raw, "context_updates")
        suggestions = _extract_suggestions(raw)

        return DiscussionResponse(
            message=reply,
            ready_to_advance=ready,
            context_updates=context_updates,
            suggestions=suggestions,
        )
