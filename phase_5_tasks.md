# Phase 5 — Dynamic Agent Spawning

**Requires Phase 4 complete** — spawned tasks are picked up by the parallel scheduler.
**Deploy after phase: Yes** — spawn_task tool is additive. Agents that don't call it behave identically.
**Pre-deploy check:** `pytest tests/test_task_injection.py` passes.

---

- [ ] **T186** Add `inject_task` method to `orchid/session.py` `type:code_generate` `p1` `model:local`

Read `orchid/session.py` first.

Add one new method to `Session`. Place it after `update_task_status`:

```python
def inject_task(
    self,
    title: str,
    agent_type: str = "developer",
    depends_on: list[str] | None = None,
    priority: int = 2,
) -> str:
    """Append a new task to tasks.md and session at runtime.

    Returns the new task ID (e.g. 'T201').
    """
    with self._lock:
        # Generate next ID: find max numeric suffix in existing task IDs
        max_n = 0
        for t in self.tasks:
            m = re.match(r"T(\d+)$", t.id)
            if m:
                max_n = max(max_n, int(m.group(1)))
        new_id = f"T{max_n + 1:03d}"

        # Build new Task object
        from orchid.memory.state import Task, TaskStatus
        new_task = Task(
            id=new_id,
            title=title,
            status=TaskStatus.TODO,
            depends_on=depends_on or [],
            priority=priority,
        )
        # Store agent type annotation in task metadata via type field
        new_task.type = f"code_generate"

        self.tasks.append(new_task)

        # Append to tasks.md so it persists across restarts
        tasks_file = self.project_dir / "tasks.md"
        dep_str = f" `needs:{','.join(depends_on)}`" if depends_on else ""
        new_line = (
            f"- [ ] **{new_id}** {title} "
            f"`type:code_generate` `p{priority}` `agent:{agent_type}`{dep_str}\n"
        )
        try:
            with open(tasks_file, "a", encoding="utf-8") as f:
                f.write(new_line)
        except OSError as e:
            logger.warning("inject_task: could not write to tasks.md: %s", e)

        logger.info("inject_task: added %s '%s'", new_id, title)
        return new_id
```

Add `import re` at the top of session.py if not already present. The `self._lock` was added in T177. `self.project_dir` is already an instance attribute.

---

- [ ] **T187** Create `orchid/tools/task_injection.py` `type:code_generate` `p1` `needs:T186` `model:local`

Create new file `orchid/tools/task_injection.py`. Define exactly one function and one module-level reference.

```python
"""Task injection tool — allows agents to spawn new tasks at runtime."""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchid.session import Session

logger = logging.getLogger(__name__)

# Module-level session reference — set by orchestrator before agent dispatch.
_active_session: Session | None = None


def set_active_session(session: Session) -> None:
    """Called by orchestrator before each task to wire the current session."""
    global _active_session
    _active_session = session


def spawn_task(
    title: str,
    agent_type: str = "developer",
    depends_on: str = "",
) -> str:
    """Add a new task to the run queue at runtime.

    Args:
        title: Task description (becomes the task title in tasks.md).
        agent_type: Agent type to run it: developer, tester, researcher, reviewer.
        depends_on: Comma-separated task IDs this task depends on (e.g. "T010,T011").
                    Empty string means no dependencies.

    Returns:
        The new task ID (e.g. "T042") or an error string starting with "[error".
    """
    if _active_session is None:
        return "[error: no active session — spawn_task only works inside an agent run]"
    dep_list = [d.strip() for d in depends_on.split(",") if d.strip()]
    try:
        new_id = _active_session.inject_task(
            title=title,
            agent_type=agent_type,
            depends_on=dep_list,
        )
        return f"Task {new_id} created: {title!r}"
    except Exception as e:
        logger.error("spawn_task failed: %s", e)
        return f"[error: {e}]"
```

No other functions or classes.

---

- [ ] **T188** Wire `spawn_task` into agent tool registry and set session before dispatch `type:code_generate` `p1` `needs:T187` `model:local`

Make changes to two files.

**File 1: `orchid/agents/base.py`** — Read first.
In `_make_project_tools(project_dir: Path)`, add `spawn_task` import and wrapper. At the top of `_make_project_tools`, import:
```python
from orchid.tools.task_injection import spawn_task as _spawn_task_fn
```
Add to the returned dict:
```python
"spawn_task": _spawn_task_fn,
```
This is outside the `git_tools.enabled` guard (spawn_task is always available).

Also add `spawn_task` to `DeveloperAgent.allowed_tools`... wait, DeveloperAgent has no `allowed_tools` (it's None = all allowed). So spawn_task is automatically available to DeveloperAgent. Good. For TesterAgent and ReviewerAgent, `spawn_task` should NOT be in their `allowed_tools` frozensets (they shouldn't spawn tasks). Do not add it to their frozensets.

**File 2: `orchid/orchestrator.py`** — Read first.
In `_execute_task`, before `agent = agent_cls(...)` is called, add:
```python
from orchid.tools.task_injection import set_active_session
set_active_session(self.session)
```

---

- [ ] **T189** Add `spawn_task` description to DeveloperAgent system prompt `type:code_generate` `p1` `needs:T188` `model:local`

Read `orchid/agents/developer.py`.

Add the following section to `DeveloperAgent.system_prompt()` return string, after the git integration section added in T165 (or at the end of the base prompt, before `+ base`):

```
## Dynamic Task Spawning
If you discover during execution that additional work is needed that goes beyond this task's scope,
you may spawn a new task:
  Action: spawn_task
  Action Input: {"title": "Write unit tests for the new parser", "agent_type": "tester", "depends_on": ""}

Rules:
- Only spawn tasks for clearly separable work that would make THIS task too large.
- Set depends_on to the current task ID if the spawned task needs your output.
- agent_type must be one of: developer, tester, researcher, reviewer.
- Do NOT spawn tasks to avoid doing required work — complete what THIS task requires first.
```

---

- [ ] **T190** Create `tests/test_task_injection.py` `type:code_generate` `p1` `needs:T186,T187` `model:local`

Create file `tests/test_task_injection.py`. Write exactly 4 test functions.

```python
import pytest
from unittest.mock import MagicMock, patch
from orchid.tools.task_injection import set_active_session, spawn_task
```

**`test_spawn_task_no_session_returns_error`**: call `set_active_session(None)`. Call `spawn_task("do thing")`. Assert result starts with `"[error:"`.

**`test_spawn_task_returns_task_id`**: create mock session where `inject_task(...)` returns `"T042"`. Call `set_active_session(mock_session)`. Call `spawn_task("write tests", "tester", "")`. Assert `"T042"` in result.

**`test_spawn_task_passes_deps`**: mock session. Call `spawn_task("verify output", "tester", "T010,T011")`. Assert `mock_session.inject_task` was called with `depends_on=["T010", "T011"]`.

**`test_inject_task_appends_to_tasks_md(tmp_path)`**: create minimal session-like object or call `Session` directly. Write a minimal `tasks.md` to `tmp_path`. Create session pointing to `tmp_path`. Call `session.inject_task("New task", "developer")`. Read `tasks.md`, assert the last line contains the new task ID and `"New task"`.

For the last test: import and instantiate `Session` from `orchid.session`. You may need to mock `VectorMemory` and config. Simplest approach: mock `Session` and just test the `inject_task` method's file-write behavior by calling it directly on a minimal object with `_lock = threading.RLock()`, `tasks = []`, `project_dir = tmp_path`.

---

- [ ] **T191** Review dynamic spawning implementation `type:code_review` `p1` `needs:T190`

Review files: `orchid/session.py` (`inject_task` method only), `orchid/tools/task_injection.py`, `orchid/orchestrator.py` (`set_active_session` call only).

Check for exactly these issues:
1. **Thread safety of `_active_session`** — `set_active_session` sets a module-level global. If two tasks run in parallel (Phase 4), each calls `set_active_session` before its agent runs. Could task B overwrite task A's session reference while task A's agent is mid-run? Report FAIL if the global is not thread-local.
2. **inject_task ID collision** — if two tasks run `inject_task` concurrently, could they both compute the same `max_n` and generate the same ID? Report PASS if `_lock` prevents this (both run under `with self._lock`).
3. **tasks.md append atomicity** — the file append in `inject_task` uses `open("a")`. Under concurrent appends, could lines interleave? Report the risk level.
4. **spawn_task in TesterAgent** — is `spawn_task` absent from TesterAgent and ReviewerAgent tool sets? Report PASS or FAIL.

---

- [ ] **T192** Fix issues found in T191 `type:code_generate` `p1` `needs:T191` `model:local`

Read T191 review results. For each FAIL, apply minimal fix:

- Issue 1 (thread-local session): replace the module-level `_active_session` global with `threading.local()`. Change `_active_session: Session | None = None` to `_local = threading.local()`. Change `set_active_session` to `_local.session = session`. Change `spawn_task` to read `_active_session = getattr(_local, "session", None)`. Add `import threading` at top. This ensures each thread has its own session reference.
- Issue 3 (file append atomicity): wrap the file append with a module-level `threading.Lock()` named `_file_lock`. Acquire before `open("a")`, release after. Add `_file_lock = threading.Lock()` as module-level in session.py or inject_task.py.

Apply only fixes for flagged FAILs. If no FAILs, write `Final Answer: No fixes needed.`
