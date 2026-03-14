"""State management: tasks.md task board + CLAUDE.md hot memory."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from orchid import config as cfg


class TaskStatus(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


@dataclass
class Task:
    id: str
    title: str
    status: TaskStatus = TaskStatus.TODO
    type: str = "draft"           # determines model routing
    priority: int = 2             # 1=high, 2=normal, 3=low
    description: str = ""
    agent: Optional[str] = None   # assigned agent class
    tags: list[str] = field(default_factory=list)

    def to_md_line(self) -> str:
        tag_str = " ".join(f"#{t}" for t in self.tags)
        parts = [
            f"- [{self._status_char()}]",
            f"**{self.id}**",
            f"{self.title}",
            f"`type:{self.type}`",
            f"`p{self.priority}`",
        ]
        if self.agent:
            parts.append(f"`agent:{self.agent}`")
        if tag_str:
            parts.append(tag_str)
        return " ".join(parts)

    def _status_char(self) -> str:
        return {
            TaskStatus.TODO: " ",
            TaskStatus.IN_PROGRESS: ">",
            TaskStatus.DONE: "x",
            TaskStatus.BLOCKED: "!",
            TaskStatus.CANCELLED: "-",
        }[self.status]


# ── Tasks.md parser/writer ────────────────────────────────────────────────────

_TASK_RE = re.compile(
    r"^- \[(?P<sc>.)\]\s+\*\*(?P<id>[^*]+)\*\*\s+(?P<title>[^`\n]+)"
    r"(?:`type:(?P<type>[^`]+)`)?"
    r"(?:\s+`p(?P<pri>\d)`)?"
    r"(?:\s+`agent:(?P<agent>[^`]+)`)?"
    r"(?P<rest>.*)$"
)
_SC_MAP = {" ": TaskStatus.TODO, ">": TaskStatus.IN_PROGRESS,
           "x": TaskStatus.DONE, "!": TaskStatus.BLOCKED, "-": TaskStatus.CANCELLED}


def load_tasks(project_dir: str | Path = ".") -> list[Task]:
    path = Path(project_dir) / cfg.get("memory.tasks_file", "tasks.md")
    if not path.exists():
        return []
    tasks = []
    in_comment = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if "<!--" in stripped:
            in_comment = True
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        m = _TASK_RE.match(stripped)
        if not m:
            continue
        tags = re.findall(r"#(\w+)", m.group("rest") or "")
        tasks.append(Task(
            id=m.group("id").strip(),
            title=m.group("title").strip(),
            status=_SC_MAP.get(m.group("sc"), TaskStatus.TODO),
            type=m.group("type") or "draft",
            priority=int(m.group("pri") or 2),
            agent=m.group("agent"),
            tags=tags,
        ))
    return tasks


def save_tasks(tasks: list[Task], project_dir: str | Path = ".") -> None:
    path = Path(project_dir) / cfg.get("memory.tasks_file", "tasks.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    groups: dict[TaskStatus, list[Task]] = {s: [] for s in TaskStatus}
    for t in tasks:
        groups[t.status].append(t)

    lines = ["# Tasks\n"]
    for status in [TaskStatus.IN_PROGRESS, TaskStatus.TODO, TaskStatus.BLOCKED,
                   TaskStatus.DONE, TaskStatus.CANCELLED]:
        bucket = groups[status]
        if not bucket:
            continue
        lines.append(f"\n## {status.value}\n")
        for t in sorted(bucket, key=lambda x: x.priority):
            lines.append(t.to_md_line())
            if t.description:
                lines.append(f"  - {t.description}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def next_task(tasks: list[Task]) -> Task | None:
    """Pick the highest-priority TODO task."""
    candidates = [t for t in tasks if t.status == TaskStatus.TODO]
    if not candidates:
        return None
    return min(candidates, key=lambda t: t.priority)


# ── CLAUDE.md hot memory ──────────────────────────────────────────────────────

def load_hot_memory(project_dir: str | Path = ".") -> str:
    path = Path(project_dir) / cfg.get("memory.hot_memory_file", "CLAUDE.md")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_hot_memory(content: str, project_dir: str | Path = ".") -> None:
    path = Path(project_dir) / cfg.get("memory.hot_memory_file", "CLAUDE.md")
    path.write_text(content, encoding="utf-8")
