"""Decision log — append-only JSON Lines record of architectural decisions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchid import config as cfg

logger = logging.getLogger(__name__)


def _decisions_path(project_dir: str | Path = ".") -> Path:
    rel = cfg.get("memory.decisions_file", ".orchid/decisions.json")
    return Path(project_dir) / rel


def load_decisions(project_dir: str | Path = ".") -> list[dict[str, Any]]:
    path = _decisions_path(project_dir)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning(
                    "decisions.py: skipping malformed JSON on line %d: %.100s",
                    len(records) + 1,
                    line,
                )
    return records


def record_decision(
    title: str,
    decision: str,
    rationale: str = "",
    context: str = "",
    project_dir: str | Path = ".",
) -> dict[str, Any]:
    """Append a decision record and return it."""
    path = _decisions_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = load_decisions(project_dir)
    record = {
        "id": f"D{len(existing) + 1:04d}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "decision": decision,
        "rationale": rationale,
        "context": context,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def recent_decisions(n: int = 10, project_dir: str | Path = ".") -> list[dict[str, Any]]:
    return load_decisions(project_dir)[-n:]


def decisions_as_md(n: int = 10, project_dir: str | Path = ".") -> str:
    records = recent_decisions(n, project_dir)
    if not records:
        return "_No decisions recorded._"
    lines = []
    for r in records:
        ts = r.get("timestamp", "")[:10]
        lines.append(f"### {r['id']} — {r['title']} ({ts})")
        lines.append(f"**Decision:** {r['decision']}")
        if r.get("rationale"):
            lines.append(f"**Rationale:** {r['rationale']}")
        lines.append("")
    return "\n".join(lines)
