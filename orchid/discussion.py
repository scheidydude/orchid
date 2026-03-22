"""Discussion persistence for Orchid V2 conversational requirements capture.

Files:
  <project>/.orchid/discussion/conversation.jsonl  — full turn-by-turn log
  <project>/.orchid/discussion/context.md          — running decisions summary
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CONTEXT_MD = """\
## Project Intent

## Confirmed Requirements

## Tech Stack Decisions

## Out of Scope

## Open Questions
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DiscussionHistory:
    """Persistent, append-only log of a conversational requirements session."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir).resolve()
        self._dir = self.project_dir / ".orchid" / "discussion"
        self._conv_path = self._dir / "conversation.jsonl"
        self._context_path = self._dir / "context.md"
        self._entries: list[dict] = []
        self._turn: int = 0

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, project_dir: Path) -> DiscussionHistory:
        h = cls(project_dir)
        h._dir.mkdir(parents=True, exist_ok=True)
        if h._conv_path.exists():
            for line in h._conv_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    h._entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            if h._entries:
                h._turn = max(e.get("turn", 0) for e in h._entries)
        return h

    # ── Mutation ──────────────────────────────────────────────────────────────

    def append(self, role: str, message: str, phase: str = "DISCUSSING") -> None:
        """Append one turn to the conversation log."""
        self._turn += 1
        entry = {
            "turn": self._turn,
            "timestamp": _now(),
            "role": role,
            "message": message,
            "phase": phase,
        }
        self._entries.append(entry)
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(self._conv_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def update_context(self, updates: str) -> None:
        """Append agent-captured decisions/constraints to context.md."""
        if not updates or not updates.strip():
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        current = self.get_context_md()
        updated = current.rstrip() + "\n\n" + updates.strip() + "\n"
        self._context_path.write_text(updated, encoding="utf-8")

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_full_history(self) -> list[dict]:
        return list(self._entries)

    def get_recent(self, n: int = 10) -> list[dict]:
        return self._entries[-n:]

    def turn_count(self) -> int:
        return self._turn

    def get_context_md(self) -> str:
        if self._context_path.exists():
            return self._context_path.read_text(encoding="utf-8")
        return _DEFAULT_CONTEXT_MD

    def to_prompt_context(self) -> str:
        """Format the conversation history as a block for agent prompts."""
        if not self._entries:
            return ""
        lines = ["## Discussion History"]
        for e in self._entries:
            role = e.get("role", "unknown").capitalize()
            msg = e.get("message", "")
            lines.append(f"**{role} (turn {e.get('turn', '?')}):** {msg}")
        return "\n\n".join(lines)
