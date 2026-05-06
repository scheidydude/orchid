# Phase 6 — Cross-Project Agent Sharing

**Requires Phase 4 (parallelism) complete.**
**Deploy after phase: Yes** — pool is opt-in via config (`agent_pool.enabled: false` default). BackgroundRunner falls back to direct dispatch when pool is disabled.
**Pre-deploy check:** `pytest tests/test_agent_pool.py` passes.

---

- [ ] **T193** Create `orchid/agent_pool.py` `type:code_generate` `p1` `model:local`

Create new file `orchid/agent_pool.py`. Define exactly one class and one exception.

**`AgentPoolError(Exception)`**: body is `pass`.

**`AgentPool`** — a singleton that manages a shared pool of agent worker threads across projects.

```python
"""Cross-project agent pool — reuses agent threads across projects."""
from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class AgentPoolError(Exception):
    pass


@dataclass
class _AgentRequest:
    agent_type: str
    task_description: str
    session_context: str
    project_dir: str
    future: Future[str] = field(default_factory=Future)


class AgentPool:
    """Singleton shared agent pool. Projects submit agent requests; pool dispatches."""

    _instance: AgentPool | None = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> AgentPool:
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._initialized = False
                cls._instance = inst
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._queues: dict[str, queue.Queue[_AgentRequest | None]] = {}
        self._workers: dict[str, list[threading.Thread]] = {}
        self._started = False
        self._stop_event = threading.Event()
        self._config_lock = threading.Lock()

    def start(self) -> None:
        """Start worker threads. Safe to call multiple times (idempotent)."""
        from orchid import config as cfg
        with self._config_lock:
            if self._started:
                return
            self._started = True
            limits: dict[str, int] = cfg.get(
                "agent_pool.max_agents_per_type",
                {"developer": 2, "tester": 2, "researcher": 2, "reviewer": 1},
            )
            for agent_type, count in limits.items():
                self._queues[agent_type] = queue.Queue()
                self._workers[agent_type] = []
                for i in range(count):
                    t = threading.Thread(
                        target=self._worker_loop,
                        args=(agent_type,),
                        daemon=True,
                        name=f"orchid-pool-{agent_type}-{i}",
                    )
                    t.start()
                    self._workers[agent_type].append(t)
            logger.info("AgentPool started with config: %s", limits)

    def stop(self) -> None:
        """Signal all workers to stop and join them."""
        self._stop_event.set()
        for agent_type, q in self._queues.items():
            for _ in self._workers.get(agent_type, []):
                q.put(None)  # poison pill
        for threads in self._workers.values():
            for t in threads:
                t.join(timeout=5)
        self._started = False
        self._stop_event.clear()
        logger.info("AgentPool stopped")

    def submit(
        self,
        agent_type: str,
        task_description: str,
        session_context: str,
        project_dir: str,
    ) -> Future[str]:
        """Submit an agent task to the pool. Returns a Future for the result string."""
        if not self._started:
            raise AgentPoolError("AgentPool not started — call start() first")
        if agent_type not in self._queues:
            raise AgentPoolError(
                f"Unknown agent_type {agent_type!r}. "
                f"Valid: {sorted(self._queues)}"
            )
        req = _AgentRequest(
            agent_type=agent_type,
            task_description=task_description,
            session_context=session_context,
            project_dir=project_dir,
        )
        self._queues[agent_type].put(req)
        return req.future

    def _worker_loop(self, agent_type: str) -> None:
        """Worker thread: dequeue requests, run agent, set future result."""
        q = self._queues[agent_type]
        while not self._stop_event.is_set():
            try:
                req = q.get(timeout=1.0)
            except queue.Empty:
                continue
            if req is None:
                break  # poison pill
            try:
                result = self._run_agent(req)
                req.future.set_result(result)
            except Exception as e:
                logger.exception("Pool worker %s failed on task: %s", agent_type, req.task_description[:80])
                req.future.set_exception(e)
            finally:
                q.task_done()

    def _run_agent(self, req: _AgentRequest) -> str:
        """Instantiate and run agent for the request. Returns Final Answer string."""
        from orchid.agents.delegator import _get_agent_class
        from pathlib import Path
        agent_cls = _get_agent_class(req.agent_type)
        agent = agent_cls(
            session_context=req.session_context,
            project_dir=Path(req.project_dir) if req.project_dir else None,
        )
        return agent.run(req.task_description)

    @classmethod
    def reset(cls) -> None:
        """Reset singleton — for testing only."""
        with cls._lock:
            if cls._instance is not None and cls._instance._started:
                cls._instance.stop()
            cls._instance = None
```

---

- [ ] **T194** Add agent pool config to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `model:local`

Read `orchid/orchid.defaults.yaml`. Append after the `runner:` section added in T181:

```yaml

# Cross-project agent pool — shared agent workers across projects.
# Agents are stateless (session_context injected per-task), safe to share.
# enabled: false means BackgroundRunner dispatches agents directly (Phase 4 behavior).
agent_pool:
  enabled: false
  max_agents_per_type:
    developer: 2
    tester: 2
    researcher: 2
    reviewer: 1
```

---

- [ ] **T195** Wire `AgentPool` into `BackgroundRunner._run()` in `orchid/runner.py` `type:code_generate` `p1` `needs:T193,T194` `model:local`

Read `orchid/runner.py` (after Phase 4 changes).

The pool integration is an opt-in path. When `agent_pool.enabled` is True, instead of calling `orch._execute_task(task)` directly, dispatch through the pool.

Add a new method `_run_task_via_pool` to `BackgroundRunner`:

```python
def _run_task_via_pool(
    self,
    pool: "AgentPool",
    sem: threading.Semaphore,
    orch: "Orchestrator",
    task: "Task",
    session: "Session",
    state: _ProjectState,
    scheduler: "ParallelScheduler",
) -> None:
    """Run task through shared AgentPool instead of direct dispatch."""
    from orchid.memory.state import TaskStatus
    sem.acquire()
    try:
        provider, _ = orch._resolve_provider(task)
        session_context = session.context_block()
        project_dir = str(session.project_dir)
        agent_type = getattr(orch._resolve_agent(task), "agent_type", "developer")

        future = pool.submit(
            agent_type=agent_type,
            task_description=task.description or task.title,
            session_context=session_context,
            project_dir=project_dir,
        )
        result = future.result(timeout=600)  # 10 min hard timeout
        # Record result in TaskResultStore
        from orchid.memory.state import TaskResultStore
        store = TaskResultStore(session.project_dir)
        store.append(task.id, task.title, task.type, result)
        session.update_task_status(task.id, TaskStatus.DONE)
        session.save()
        state.tasks_done += 1
    except Exception:
        logger.exception("Pool task %s failed", task.id)
        session.update_task_status(task.id, TaskStatus.BLOCKED)
        session.save()
    finally:
        scheduler.mark_done(task.id)
        sem.release()
```

In `_run(self, project_path, state)`, before the scheduler loop, add:

```python
from orchid import config as cfg
from orchid.agent_pool import AgentPool
_pool_enabled = cfg.get("agent_pool.enabled", False)
_pool: AgentPool | None = None
if _pool_enabled:
    _pool = AgentPool()
    _pool.start()
```

In the loop where `self._executor.submit(self._run_task_with_semaphore, ...)` is called, change it to:

```python
if _pool is not None:
    f = self._executor.submit(
        self._run_task_via_pool, _pool, sem, orch, task, session, state, scheduler
    )
else:
    f = self._executor.submit(
        self._run_task_with_semaphore, sem, orch, task, session, state, scheduler
    )
```

In the `finally:` block, add `if _pool is not None: _pool.stop()` before the MCP disconnect.

---

- [ ] **T196** Wire `AgentPool` into `AgentDelegator.delegate()` in `orchid/agents/delegator.py` `type:code_generate` `p1` `needs:T193` `model:local`

Read `orchid/agents/delegator.py`.

When the pool is enabled and `delegation.use_pool: true`, delegation should go through the pool rather than direct agent instantiation.

Add one conditional block inside `delegate()`, after the `sub_context = self._build_sub_context(...)` line and before `agent = agent_cls(...)`:

```python
# Route through AgentPool if enabled
from orchid import config as cfg
if cfg.get("agent_pool.enabled", False) and cfg.get("delegation.use_pool", False):
    try:
        from orchid.agent_pool import AgentPool
        pool = AgentPool()
        if pool._started:
            project_dir = str(self.session.project_dir) if self.session else ""
            fut = pool.submit(
                agent_type=agent_type,
                task_description=task,
                session_context=sub_context,
                project_dir=project_dir,
            )
            result = fut.result(timeout=cfg.get("delegation.pool_timeout_s", 300))
            # Skip direct agent run below
            result_summary = result[:500]
            timestamp = datetime.now(UTC).isoformat()
            delegation_record = { ... }  # same as below
            # ... record delegation, embed, return result
            return result
    except Exception as _pool_err:
        logger.warning("[delegator] pool dispatch failed, falling back to direct: %s", _pool_err)
        # falls through to direct agent instantiation below
```

Note: the delegation_record construction and embedding code already exists below this point. Do NOT duplicate it — instead, extract it into a helper `_record_delegation(self, result, task, agent_type, depth, timestamp)` and call it from both paths.

Read the full delegate() method carefully before making changes. Keep the worktree logic from Phase 3 intact.

---

- [ ] **T197** Create `tests/test_agent_pool.py` `type:code_generate` `p1` `needs:T193` `model:local`

Create file `tests/test_agent_pool.py`. Write exactly 4 test functions.

```python
import threading
import time
from concurrent.futures import Future
from unittest.mock import MagicMock, patch
from orchid.agent_pool import AgentPool, AgentPoolError
```

Call `AgentPool.reset()` in each test or in a `setup_function` to ensure singleton is fresh.

**`test_pool_start_creates_workers`**: patch `cfg.get` to return `{"developer": 2}`. Call `pool.start()`. Assert `len(pool._workers["developer"]) == 2`. All threads are alive. Call `pool.stop()`.

**`test_pool_submit_returns_future`**: start pool with `{"developer": 1}`. Patch `AgentPool._run_agent` to return `"done"`. Submit a request. Assert returned object is a `Future`. Call `future.result(timeout=2)`. Assert result is `"done"`. Call `pool.stop()`.

**`test_pool_submit_unknown_agent_type_raises`**: start pool with `{"developer": 1}`. Assert `pool.submit("wizard", "task", "", "")` raises `AgentPoolError`. Call `pool.stop()`.

**`test_pool_stop_joins_workers`**: start pool. Call `pool.stop()`. Assert `_started` is False. Assert all worker threads are no longer alive (joined).

---

- [ ] **T198** Review agent pool implementation `type:code_review` `p1` `needs:T197`

Review files: `orchid/agent_pool.py`, `orchid/runner.py` (pool wiring only).

Check for exactly these issues:
1. **Singleton thread safety** — `AgentPool.__new__` uses a class-level `_lock`. Is the lock acquired before checking `_instance`? Could two threads simultaneously pass `_instance is None`? Report PASS or FAIL.
2. **Future double-set** — could `req.future.set_result()` and `req.future.set_exception()` both be called for the same request? This would raise `InvalidStateError`. Report PASS if only one is called (try/except/finally structure).
3. **Pool stop before start** — if `stop()` is called before `start()`, does it raise or silently return? Report the behavior.
4. **Pool timeout** — `future.result(timeout=600)` in `_run_task_via_pool`. If agent hangs, this blocks the semaphore for 10 minutes. Is this acceptable? Report as INFO (not FAIL), suggest configurable timeout.
5. **Delegation fallback** — in `delegator.py`, if pool dispatch fails, does the fallback to direct agent instantiation work correctly? Report PASS or FAIL.

---

- [ ] **T199** Fix issues found in T198 `type:code_generate` `p1` `needs:T198` `model:local`

Read T198 results. Apply minimal fixes for FAILs only:
- Issue 2 (double set_result): ensure `set_result` and `set_exception` cannot both run by checking the finally block doesn't call either. The try/except/finally should have `set_result` in try, `set_exception` in except, nothing in finally. Verify and fix if needed.
- Issue 3 (stop before start): add guard at top of `stop()`: `if not self._started: return`.

For issue 4 (timeout): make timeout configurable. In `_run_task_via_pool`, replace `timeout=600` with `timeout=cfg.get("agent_pool.task_timeout_s", 600)`. Add `task_timeout_s: 600` to `agent_pool:` section in `orchid.defaults.yaml`.

Apply only fixes for flagged FAILs (and the timeout configurability since it's specifically noted). If no FAILs, write `Final Answer: Applied timeout configurability only.`
