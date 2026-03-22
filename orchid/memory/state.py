"""State management: tasks.md task board + CLAUDE.md hot memory."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC
from enum import Enum
from pathlib import Path

from orchid import config as cfg

logger = logging.getLogger(__name__)


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
    agent: str | None = None   # assigned agent class
    tags: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)   # task IDs this task waits for
    model_override: str | None = None                  # claude | local | auto
    rollup_sources: list[str] = field(default_factory=list)  # task IDs to gather results from (rollup type)
    output_file: str | None = None                        # output filename for rollup synthesis

    def is_runnable(self, completed_ids: set[str]) -> bool:
        """True when all declared dependencies are done (includes rollup_sources)."""
        all_deps = set(self.depends_on) | set(self.rollup_sources)
        return all(dep in completed_ids for dep in all_deps)

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
        if self.depends_on:
            parts.append(f"`needs:{','.join(self.depends_on)}`")
        if self.model_override:
            parts.append(f"`model:{self.model_override}`")
        if self.rollup_sources:
            parts.append(f"`rollup:{','.join(self.rollup_sources)}`")
        if self.output_file:
            parts.append(f"`output:{self.output_file}`")
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
        rest = m.group("rest") or ""
        tags = re.findall(r"#(\w+)", rest)

        # Parse needs:T001,T002 annotation
        needs_m = re.search(r"`needs:([^`]+)`", rest)
        depends_on = (
            [d.strip() for d in needs_m.group(1).split(",") if d.strip()]
            if needs_m else []
        )

        # Parse model:claude|local|auto annotation
        model_m = re.search(r"`model:([^`]+)`", rest)
        model_override = model_m.group(1).strip() if model_m else None

        # Parse rollup:T001,T002 annotation
        rollup_m = re.search(r"`rollup:([^`]+)`", rest)
        rollup_sources = (
            [d.strip() for d in rollup_m.group(1).split(",") if d.strip()]
            if rollup_m else []
        )

        # Parse output:FILENAME.md annotation
        output_m = re.search(r"`output:([^`]+)`", rest)
        output_file = output_m.group(1).strip() if output_m else None

        tasks.append(Task(
            id=m.group("id").strip(),
            title=m.group("title").strip(),
            status=_SC_MAP.get(m.group("sc"), TaskStatus.TODO),
            type=m.group("type") or "draft",
            priority=int(m.group("pri") or 2),
            agent=m.group("agent"),
            tags=tags,
            depends_on=depends_on,
            model_override=model_override,
            rollup_sources=rollup_sources,
            output_file=output_file,
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


def detect_dependency_cycles(tasks: list[Task]) -> list[list[str]]:
    """Return list of cycle paths detected in the dependency graph."""
    task_map = {t.id: t for t in tasks}
    cycles: list[list[str]] = []
    visited: set[str] = set()

    def dfs(task_id: str, path: list[str]) -> None:
        if task_id in path:
            cycle_start = path.index(task_id)
            cycles.append(path[cycle_start:] + [task_id])
            return
        if task_id in visited:
            return
        visited.add(task_id)
        task = task_map.get(task_id)
        if task:
            for dep in task.depends_on:
                dfs(dep, path + [task_id])

    for task in tasks:
        if task.id not in visited:
            dfs(task.id, [])
    return cycles


def next_task(tasks: list[Task]) -> Task | None:
    """Pick the highest-priority runnable TODO task."""
    completed_ids = {t.id for t in tasks if t.status == TaskStatus.DONE}

    if cfg.get("dependencies.enabled", True) and cfg.get("dependencies.cycle_detection", True):
        todo_tasks = [t for t in tasks if t.status == TaskStatus.TODO]
        if any(t.depends_on for t in todo_tasks):
            cycles = detect_dependency_cycles(todo_tasks)
            if cycles:
                logger.warning("Circular dependencies detected: %s", cycles)

    candidates = [
        t for t in tasks
        if t.status == TaskStatus.TODO and t.is_runnable(completed_ids)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda t: t.priority)


# ── Task result store ─────────────────────────────────────────────────────────


class TaskResultStore:
    """Persistent store for completed task results (.orchid/task_results.json).

    Stored as JSON Lines — one entry per completed task.  Used by rollup tasks
    to gather results without re-parsing session logs.
    """

    def __init__(self, project_dir: str | Path) -> None:
        self._path = Path(project_dir) / ".orchid" / "task_results.json"

    def append(self, task_id: str, title: str, task_type: str, result: str) -> None:
        """Append a completed task result to the store."""
        from datetime import datetime
        self._path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "task_id": task_id,
            "title": title,
            "type": task_type,
            "completed_at": datetime.now(UTC).isoformat(),
            "result": result,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        entries = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed line in task_results.json")
        return entries

    def get(self, task_id: str) -> dict | None:
        """Return the most recent stored result for task_id, or None."""
        for entry in reversed(self._read_all()):
            if entry.get("task_id") == task_id:
                return entry
        return None

    def get_many(self, task_ids: list[str]) -> list[dict]:
        """Return the most recent result for each task_id, preserving order."""
        all_entries = self._read_all()
        latest: dict[str, dict] = {}
        for entry in all_entries:
            tid = entry.get("task_id", "")
            if tid in task_ids:
                latest[tid] = entry
        return [latest[tid] for tid in task_ids if tid in latest]

    def get_all(self) -> list[dict]:
        """Return all stored results."""
        return self._read_all()


# ── CLAUDE.md hot memory ──────────────────────────────────────────────────────

def load_hot_memory(project_dir: str | Path = ".") -> str:
    path = Path(project_dir) / cfg.get("memory.hot_memory_file", "CLAUDE.md")
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_hot_memory(content: str, project_dir: str | Path = ".") -> None:
    path = Path(project_dir) / cfg.get("memory.hot_memory_file", "CLAUDE.md")
    path.write_text(content, encoding="utf-8")
