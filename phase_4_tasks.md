# Phase 4 — Parallelism / Concurrent Agent Execution

**Deploy after phase: Yes, with caution.** Test on a project with ≥3 independent tasks before deploying to production. The run loop is rewritten; sequential behavior is preserved when all tasks have `needs:` dependencies.
**Pre-deploy check:** `pytest tests/test_scheduler.py tests/test_parallel_runner.py` pass. Run one full project end-to-end manually.

---

- [ ] **T176** Create `orchid/scheduler.py` `type:code_generate` `p1` `model:local`

Create new file `orchid/scheduler.py`. Define exactly one class.

**`ParallelScheduler`**:
Constructor: `__init__(self, session: "Session")`. Stores `self._session = session`. Creates `self._in_flight: set[str] = set()` and `self._lock = threading.Lock()`.

**`ready_tasks(self) -> list[Task]`** — thread-safe.
- Acquire `self._lock`.
- Read `completed_ids = {t.id for t in self._session.tasks if t.status in (TaskStatus.DONE, TaskStatus.SKIPPED, TaskStatus.BLOCKED)}`.
- Return `[t for t in self._session.tasks if t.status == TaskStatus.TODO and t.id not in self._in_flight and t.is_runnable(completed_ids)]`.
- Sorted by `t.priority` ascending (lower number = higher priority, matches existing `next_task` behavior).

**`mark_in_flight(self, task_id: str) -> None`** — acquire lock, add `task_id` to `_in_flight`.

**`mark_done(self, task_id: str) -> None`** — acquire lock, discard `task_id` from `_in_flight`.

**`all_done(self) -> bool`** — acquire lock. Return `True` if all tasks have status in `{TaskStatus.DONE, TaskStatus.SKIPPED, TaskStatus.BLOCKED}` and `_in_flight` is empty.

Imports:
```python
from __future__ import annotations
import threading
from typing import TYPE_CHECKING
from orchid.memory.state import Task, TaskStatus
if TYPE_CHECKING:
    from orchid.session import Session
```

---

- [ ] **T177** Add threading lock to `orchid/session.py` `type:code_generate` `p1` `model:local`

Read `orchid/session.py` first.

Make exactly three changes:

**Change 1** — add `import threading` to the imports section at the top.

**Change 2** — in `Session.__init__` (or `Session.load()` — whichever initializes instance state), add after the first `self.` assignment:
```python
self._lock = threading.RLock()
```

**Change 3** — wrap the bodies of these three methods with `with self._lock:`:
- `update_task_status(self, task_id, status)` — wrap the entire for loop body.
- `save(self)` — wrap the entire method body.
- `log_event(self, event_type, data)` — wrap the entire method body.

Do not lock `next_task()` — it is replaced by `ParallelScheduler.ready_tasks()` in Phase 4. Do not modify any other methods.

---

- [ ] **T178** Extract `_resolve_provider` method from `_execute_task` in `orchid/orchestrator.py` `type:code_generate` `p1` `model:local`

Read `orchid/orchestrator.py`. Find `_execute_task(self, task: Task)`. The provider resolution block starts approximately at the line `agent_cls = self._resolve_agent(task)` and ends at `decision = RouteDecision(model="local", ...)` (the offline mode override).

Extract that entire block (from `agent_cls = self._resolve_agent(task)` through the offline mode override) into a new method:

```python
def _resolve_provider(self, task: Task) -> tuple[str, "RouteDecision"]:
    """Resolve agent class and provider for a task without executing it."""
    from orchid.providers.registry import get_registry as _get_provider_registry
    from orchid.tools.models import RouteDecision
    agent_cls = self._resolve_agent(task)
    agent_type = getattr(agent_cls, "agent_type", "base")
    per_agent_override = self.cli_provider_overrides.get(agent_type)
    _agent_name = getattr(agent_cls, "agent_name", agent_type)
    _provider_name = _get_provider_registry().resolve_name(
        agent_type=agent_type,
        agent_name=_agent_name,
        task_type=task.type,
        task_model=task.model_override,
        cli_override=per_agent_override or self.cli_model_override,
        task_title=task.title,
    )
    decision = RouteDecision(model=_provider_name, reason="registry", source="registry")
    if self.offline_mode:
        decision = RouteDecision(model="local", reason="offline mode", source="cli_flag")
    return decision.model, decision
```

In `_execute_task`, replace the extracted block with:
```python
_provider_name, decision = self._resolve_provider(task)
```
And update any references to `decision` below — they should work unchanged since `decision` is still set.

Return type is `tuple[str, RouteDecision]`. Add `RouteDecision` to the `TYPE_CHECKING` import if needed.

---

- [ ] **T179** Add provider semaphores to `BackgroundRunner` in `orchid/runner.py` `type:code_generate` `p1` `needs:T177` `model:local`

Read `orchid/runner.py` first.

Make exactly three changes:

**Change 1** — in `BackgroundRunner.__init__`, add:
```python
self._semaphore_lock = threading.Lock()
self._provider_semaphores: dict[str, threading.Semaphore] = {}
```

**Change 2** — add new method `_get_semaphore(self, provider: str) -> threading.Semaphore`:
```python
def _get_semaphore(self, provider: str) -> threading.Semaphore:
    with self._semaphore_lock:
        if provider not in self._provider_semaphores:
            limits = cfg.get("runner.provider_concurrency", {"local": 3, "anthropic": 3})
            limit = limits.get(provider, 1)
            self._provider_semaphores[provider] = threading.Semaphore(limit)
        return self._provider_semaphores[provider]
```

**Change 3** — add new method `_run_task_with_semaphore`:
```python
def _run_task_with_semaphore(
    self,
    sem: threading.Semaphore,
    orch: "Orchestrator",
    task: "Task",
    session: "Session",
    state: _ProjectState,
    scheduler: "ParallelScheduler",
) -> None:
    sem.acquire()
    try:
        orch._execute_task(task)
        session.save()
        state.tasks_done += 1
    except Exception:
        logger.exception("Task %s failed in parallel execution", task.id)
        from orchid.memory.state import TaskStatus
        session.update_task_status(task.id, TaskStatus.BLOCKED)
        session.save()
    finally:
        scheduler.mark_done(task.id)
        sem.release()
```

Add `from orchid import config as cfg` at top of file if not already present. Add `import threading` if not already present. Add TYPE_CHECKING imports for `Orchestrator`, `Task`, `Session`, `ParallelScheduler` if needed.

---

- [ ] **T180** Rewrite `BackgroundRunner._run()` loop for parallel dispatch `type:code_generate` `p1` `needs:T176,T177,T178,T179` `model:local`

Read `orchid/runner.py` (after T179 changes). Find `_run(self, project_path: str, state: _ProjectState)`.

Replace the sequential task loop (the `while not state.cancel_event.is_set():` block that calls `session.next_task()` and `orch._execute_task(task)`) with:

```python
from concurrent.futures import Future, wait, FIRST_COMPLETED
from orchid.memory.state import TaskStatus
from orchid.scheduler import ParallelScheduler

scheduler = ParallelScheduler(session)
active_futures: set[Future] = set()

while not state.cancel_event.is_set():
    ready = scheduler.ready_tasks()
    for task in ready:
        provider, _ = orch._resolve_provider(task)
        sem = self._get_semaphore(provider)
        scheduler.mark_in_flight(task.id)
        session.update_task_status(task.id, TaskStatus.IN_PROGRESS)
        state.current_task = f"{task.id}: {task.title}"
        f = self._executor.submit(
            self._run_task_with_semaphore, sem, orch, task, session, state, scheduler
        )
        active_futures.add(f)

    if active_futures:
        done, active_futures = wait(active_futures, timeout=1.0, return_when=FIRST_COMPLETED)
        for f in done:
            exc = f.exception()
            if exc:
                logger.error("Parallel task raised uncaught exception: %s", exc)
    elif scheduler.all_done():
        break
    else:
        # In-flight tasks blocking remaining deps — wait briefly
        time.sleep(0.5)

# Drain any remaining futures before cleanup
if active_futures:
    wait(active_futures)
```

Keep all code outside this loop unchanged (session setup, MCP setup, emitter setup, finally block). Keep the `try:` and `except Exception:` wrapping the loop. The `session.save()` call that was after `orch._execute_task(task)` is now inside `_run_task_with_semaphore` — remove it from the outer loop (it no longer exists).

---

- [ ] **T181** Add `runner.provider_concurrency` to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `model:local`

Read `orchid/orchid.defaults.yaml`. Append after the `delegation:` section added in T171:

```yaml

# Parallel task execution — max concurrent tasks per provider.
# Each provider gets its own semaphore. Unknown providers default to 1 (sequential).
runner:
  provider_concurrency:
    local: 3
    anthropic: 3
```

---

- [ ] **T182** Create `tests/test_scheduler.py` `type:code_generate` `p1` `needs:T176` `model:local`

Create file `tests/test_scheduler.py`. Write exactly 5 test functions. Build `Session` mock using `MagicMock` with a `.tasks` list of `Task` objects (import `Task` and `TaskStatus` from `orchid.memory.state`).

```python
from unittest.mock import MagicMock
from orchid.memory.state import Task, TaskStatus
from orchid.scheduler import ParallelScheduler
```

Helper: `def make_task(id, status=TaskStatus.TODO, depends_on=None, priority=1)` — returns `Task(id=id, title=id, status=status, depends_on=depends_on or [], priority=priority)`.

**`test_ready_returns_todo_with_no_deps`**: session.tasks = [T1 (TODO, no deps), T2 (DONE)]. `ready_tasks()` returns `[T1]`.

**`test_ready_excludes_in_flight`**: session.tasks = [T1 (TODO), T2 (TODO)]. Call `mark_in_flight("T1")`. `ready_tasks()` returns `[T2]` only.

**`test_ready_respects_needs`**: session.tasks = [T1 (TODO, depends_on=["T2"]), T2 (TODO)]. `ready_tasks()` returns `[T2]` only (T1 blocked by pending T2).

**`test_ready_unblocks_after_dep_done`**: session.tasks = [T1 (TODO, depends_on=["T2"]), T2 (DONE)]. `ready_tasks()` returns `[T1]`.

**`test_all_done_true_when_complete`**: session.tasks = [T1 (DONE), T2 (BLOCKED)]. `all_done()` returns `True`. Add T3 (TODO) to tasks, `all_done()` returns `False`.

---

- [ ] **T183** Create `tests/test_parallel_runner.py` `type:code_generate` `p1` `needs:T176,T177,T178,T179,T180` `model:local`

Create file `tests/test_parallel_runner.py`. Write exactly 3 test functions. Mock heavy dependencies.

```python
import threading
import time
from unittest.mock import MagicMock, patch
from orchid.runner import BackgroundRunner
```

**`test_independent_tasks_run_concurrently`**: Create 3 tasks (T1, T2, T3, all TODO, no deps). Mock `orch._execute_task` to sleep 0.05s then set a flag. Mock `orch._resolve_provider` to return `("local", MagicMock())`. Start runner, wait up to 1s. Assert all 3 tasks ran (check flags set). Assert total wall time < 0.3s (they ran in parallel, not 3×0.05s sequentially). Skip if CI is slow (use `pytest.mark.slow`).

**`test_semaphore_limits_provider_concurrency`**: Create runner, configure `runner.provider_concurrency = {"local": 1}`. Create 2 tasks. Track concurrent execution count (increment counter on enter, decrement on exit of mock `_execute_task`). Assert max concurrent count never exceeds 1.

**`test_dependent_task_waits_for_parent`**: T1 (TODO, no deps), T2 (TODO, depends_on=["T1"]). Mock `_execute_task` records call order. Assert T1 called before T2. Assert T2 not called until T1 is marked DONE in session.

Note: these tests require significant mocking of `Session`, `Orchestrator`, and config. Use `MagicMock` for both. Set `session.tasks` directly on the mock. Wire `scheduler = ParallelScheduler(session)` manually if needed to verify scheduler behavior independently.

---

- [ ] **T184** Review parallelism implementation `type:code_review` `p1` `needs:T182,T183`

Review files: `orchid/scheduler.py`, `orchid/session.py` (lock additions only), `orchid/runner.py` (`_run()` rewrite and new methods).

Check for exactly these issues:
1. **Scheduler lock** — does `ready_tasks()` hold the lock for the entire read of `session.tasks`? If `session.tasks` is mutated while `ready_tasks()` iterates it (another thread calls `update_task_status`), can this cause a `RuntimeError: list changed size during iteration`? Report PASS if the scheduler iterates a copy or if session lock prevents concurrent mutation.
2. **Double-dispatch** — could the same task be dispatched twice? Walk through: `ready_tasks()` returns T1, caller calls `mark_in_flight("T1")`, next call to `ready_tasks()` excludes T1 — is there a race between the two calls? Report PASS or FAIL.
3. **Semaphore leak** — if `_run_task_with_semaphore` raises before `sem.release()` (impossible since it's in finally, but verify). Report PASS if `sem.release()` is in `finally`.
4. **Session RLock** — `save()` is called inside `_run_task_with_semaphore`. If `_execute_task` also calls `save()` internally (it may call `save_tasks` directly), will RLock allow re-entry from same thread? Report PASS if RLock is used (not Lock).
5. **all_done() with in-flight** — if tasks are in-flight (status still IN_PROGRESS, not yet DONE), does `all_done()` return False? This prevents the loop from exiting prematurely. Report PASS or FAIL.

---

- [ ] **T185** Fix issues found in T184 `type:code_generate` `p1` `needs:T184` `model:local`

Read T184 review results. For each FAIL, apply minimal fix:
- Issue 1 (list mutation during iteration): in `ready_tasks()`, iterate over `list(self._session.tasks)` (a copy).
- Issue 2 (double-dispatch race): this is architectural — `mark_in_flight` and `ready_tasks()` share `self._lock`, so the caller must call `mark_in_flight` while still holding context that prevents another call to `ready_tasks()`. The current runner calls them sequentially in a single thread (the runner loop itself is single-threaded). Report as PASS.
- Issue 4 (Lock vs RLock): if `threading.Lock` was used instead of `RLock`, replace with `threading.RLock()`.
- Issue 5 (all_done with in-flight): `all_done()` should return `False` if `_in_flight` is non-empty. Verify and fix if needed.

Apply only fixes for flagged FAILs. If no FAILs, write `Final Answer: No fixes needed.`
