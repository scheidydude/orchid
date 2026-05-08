# Tier 1 — Foundation Tasks
# Subprocess isolation · Cancellation tokens · Wall-clock timeout · Stuck-task watchdog · Cycle detection
# Start at T209 (last used: T208). Copy this file content into tasks.md and run.
# Claude Code validates after this tier completes.

## DONE

## TODO

- [ ] **T209** Create `orchid/worker_protocol.py`. Define exactly 3 dataclasses using `@dataclass` from `dataclasses`. Import `json`, `field`, `asdict` from `dataclasses`. `type:code_generate` `p1` `model:local`
  - `TaskContext(task_id: str, task_description: str, session_context: str, agent_type: str, model_key: str, project_dir: str, injection_queue_path: str)` — all required, no defaults
  - `TaskContext.to_json(self) -> str` — returns `json.dumps(asdict(self))`
  - `TaskContext.from_json(cls, s: str) -> "TaskContext"` — classmethod, returns `cls(**json.loads(s))`
  - `WorkerEvent(type: str, task_id: str, payload: dict = field(default_factory=dict))` — payload defaults to empty dict
  - `WorkerEvent.to_json(self) -> str` — returns `json.dumps({"type": self.type, "task_id": self.task_id, **self.payload})`
  - `WorkerResult(task_id: str, success: bool, result: str = "", error: str = "", duration_s: float = 0.0)` — result/error/duration_s have defaults shown
  - `WorkerResult.to_json(self) -> str` — returns `json.dumps(asdict(self))`
  - All 3 classes importable from `orchid.worker_protocol`. No other code in the file.
  - Verify: `grep -n "class TaskContext\|class WorkerEvent\|class WorkerResult" orchid/worker_protocol.py` must return 3 lines

- [ ] **T210** Create `orchid/worker_subprocess.py`. This is the subprocess entry point — run by the parent via `sys.executable -m orchid.worker_subprocess`. `type:code_generate` `p1` `model:local` `needs:T209`
  - Imports: `import json, sys, time, logging` from stdlib. `from pathlib import Path`. `from orchid.worker_protocol import TaskContext, WorkerEvent, WorkerResult`
  - `def _make_emit(task_id: str):` — returns a `Callable[[dict], None]` that constructs a `WorkerEvent(type=payload.get("action", "agent_step"), task_id=task_id, payload=payload)` and writes `event.to_json() + "\n"` to `sys.stdout`, then calls `sys.stdout.flush()`
  - `def main() -> None:` — reads exactly one line from `sys.stdin.readline()`, deserializes as `TaskContext.from_json(line)`. Calls `_run(ctx)`. Writes the returned `WorkerResult.to_json() + "\n"` to `sys.stdout` and flushes.
  - `def _run(ctx: TaskContext) -> WorkerResult:` — imports `_get_registry` from `orchid.orchestrator`. Gets `agent_cls = _get_registry().get(ctx.agent_type, _get_registry()["base"])`. Instantiates agent: `agent = agent_cls(session_context=ctx.session_context, project_dir=Path(ctx.project_dir), stream_callback=_make_emit(ctx.task_id), injection_queue_path=Path(ctx.injection_queue_path))`. Sets `agent.model_key = ctx.model_key`. Calls `start = time.monotonic()`. Inside try/except: `result = agent.run(ctx.task_description)`, returns `WorkerResult(task_id=ctx.task_id, success=True, result=result, duration_s=time.monotonic()-start)`. On any `Exception as e`: returns `WorkerResult(task_id=ctx.task_id, success=False, error=str(e), duration_s=time.monotonic()-start)`.
  - Bottom of file: `if __name__ == "__main__": main()`
  - Verify: `grep -n "def main\|def _run\|def _make_emit\|__name__" orchid/worker_subprocess.py` must return 4 lines

- [ ] **T211** Create `orchid/subprocess_runner.py`. One class: `SubprocessRunner`. `type:code_generate` `p1` `model:local` `needs:T209`
  - Imports: `import json, logging, subprocess, sys` from stdlib. `from collections.abc import Callable`. `from orchid.worker_protocol import TaskContext, WorkerEvent, WorkerResult`
  - `class SubprocessRunner:` — no `__init__` needed (stateless)
  - `def run_task_isolated(self, ctx: TaskContext, stream_callback: Callable[[dict], None] | None = None, timeout_s: float | None = None) -> WorkerResult:` method body:
    - Spawn: `proc = subprocess.Popen([sys.executable, "-m", "orchid.worker_subprocess"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)`
    - Write: `proc.stdin.write(ctx.to_json() + "\n")`, `proc.stdin.flush()`, `proc.stdin.close()`
    - Read loop: iterate `proc.stdout` line by line. For each line, strip it. If empty skip. Try `data = json.loads(line)`. If `"success" in data`, set `worker_result = WorkerResult(**data)`. Else if `stream_callback is not None`, call `stream_callback(data)`. On `json.JSONDecodeError` pass.
    - After loop: `proc.wait(timeout=int(timeout_s) if timeout_s else None)` inside try/except `subprocess.TimeoutExpired`: call `proc.kill()`, set `worker_result = WorkerResult(task_id=ctx.task_id, success=False, error=f"Worker timed out after {timeout_s}s")`.
    - Return `worker_result` if set, else `WorkerResult(task_id=ctx.task_id, success=False, error="Worker exited without result")`
  - Verify: `grep -n "class SubprocessRunner\|def run_task_isolated" orchid/subprocess_runner.py` must return 2 lines

- [ ] **T212** Append isolation config block to `orchid/orchid.defaults.yaml`. Read the file first to find its end. Append exactly this block at the bottom. `type:code_generate` `p1` `model:local`
  - Append exactly:
    ```yaml
    # T212: Subprocess isolation settings
    isolation:
      subprocess_enabled: false   # true = each task runs in a child process
      max_task_seconds: 0         # wall-clock timeout per task (0 = no limit)
      container_enabled: false    # true = use docker container (Tier 3)
    ```
  - Verify: `grep -n "subprocess_enabled\|max_task_seconds" orchid/orchid.defaults.yaml` must return 2 lines

- [ ] **T213** Extend `orchid/orchestrator.py` — add `_run_task_isolated()` method. Read the file first. Find the method `_resolve_provider` (around line 297). Add the new method BEFORE `_resolve_provider`. `type:code_generate` `p1` `model:local` `needs:T211`
  - Add this method to the `Orchestrator` class:
    ```
    def _run_task_isolated(
        self,
        task: Task,
        plan: str,
        session_context: str,
        stream_cb: Callable | None,
        agent_type: str,
        decision: RouteDecision,
    ) -> str:
        from orchid.worker_protocol import TaskContext
        from orchid.subprocess_runner import SubprocessRunner
        injection_queue = self.session.project_dir / ".orchid" / "inject.queue"
        ctx = TaskContext(
            task_id=task.id,
            task_description=plan,
            session_context=session_context,
            agent_type=agent_type,
            model_key=decision.model,
            project_dir=str(self.session.project_dir),
            injection_queue_path=str(injection_queue),
        )
        max_s = cfg.get("isolation.max_task_seconds", 0)
        runner = SubprocessRunner()
        wresult = runner.run_task_isolated(
            ctx=ctx,
            stream_callback=stream_cb,
            timeout_s=float(max_s) if max_s else None,
        )
        if not wresult.success:
            raise RuntimeError(f"Worker subprocess failed: {wresult.error}")
        return wresult.result
    ```
  - Verify: `grep -n "def _run_task_isolated" orchid/orchestrator.py` must return 1 line

- [ ] **T214** Extend `orchid/orchestrator.py` — wire subprocess opt-in into `_execute_task()`. Read the file first. Find the block where `agent.run(plan)` is called (search for `agent.run(`). Replace the `result = agent.run(plan)` call (or equivalent call to run the agent) with an if/else that checks config. `type:code_generate` `p1` `model:local` `needs:T213`
  - Find the line that calls `agent.run(` in `_execute_task()`. Wrap it as follows:
    ```python
    if cfg.get("isolation.subprocess_enabled", False):
        result = self._run_task_isolated(
            task=task,
            plan=plan,
            session_context=session_context,
            stream_cb=stream_cb,
            agent_type=agent_type,
            decision=decision,
        )
    else:
        result = agent.run(plan)
    ```
  - Do not change anything else. Keep `result` assigned the same way in both branches.
  - Verify: `grep -n "subprocess_enabled\|_run_task_isolated" orchid/orchestrator.py` must return at least 2 lines

- [ ] **T215** Extend `orchid/agents/base.py` — add `AgentCancelledError` exception class and `cancel_event` attribute. Read the file first. Find the class definitions near the top (look for other exception classes or the BaseAgent class definition around line 288). `type:code_generate` `p1` `model:local`
  - Add `import threading` to the imports at the top of the file if not already present
  - Add this exception class BEFORE the `BaseAgent` class definition: `class AgentCancelledError(Exception): """Raised when the agent's cancel_event is set mid-run."""`
  - In `BaseAgent.__init__()`, add this line after `self.max_iterations = ...`: `self._cancel_event: threading.Event = threading.Event()`
  - Add this method to `BaseAgent` after `__init__`: `def cancel(self) -> None: """Signal the agent to stop after the current iteration.""" self._cancel_event.set()`
  - Verify: `grep -n "AgentCancelledError\|_cancel_event\|def cancel" orchid/agents/base.py` must return 3 lines

- [ ] **T216** Extend `orchid/agents/base.py` — check cancel_event at the top of each ReAct iteration. Read the file first. Find the `run()` method and the `for iteration in range(self.max_iterations):` loop (around line 484). `type:code_generate` `p1` `model:local` `needs:T215`
  - Add this check as the FIRST statement inside the for loop body, BEFORE the existing `self._check_injection_queue()` call:
    ```python
    if self._cancel_event.is_set():
        raise AgentCancelledError(f"Task cancelled after {iteration} iterations")
    ```
  - Do not change anything else in the loop.
  - Verify: `grep -n "AgentCancelledError\|_cancel_event.is_set" orchid/agents/base.py` must return 2 lines

- [ ] **T217** Extend `orchid/orchestrator.py` — start a cancellation timer before calling `agent.run()`. Read the file first. Find where `agent.run(plan)` is called in `_execute_task()` (the `else:` branch added in T214). `type:code_generate` `p1` `model:local` `needs:T216`
  - Add these lines BEFORE the `if cfg.get("isolation.subprocess_enabled"...)` block:
    ```python
    # T217: Start wall-clock cancellation timer if max_task_seconds is set
    _max_s = cfg.get("isolation.max_task_seconds", 0)
    _cancel_timer: threading.Timer | None = None
    if _max_s and _max_s > 0 and not cfg.get("isolation.subprocess_enabled", False):
        _cancel_timer = threading.Timer(_max_s, agent.cancel)
        _cancel_timer.daemon = True
        _cancel_timer.start()
    ```
  - Add a `finally:` clause after the `if cfg.get("isolation.subprocess_enabled"...)` block that cancels the timer: `if _cancel_timer is not None: _cancel_timer.cancel()`
  - Add `import threading` to the orchestrator's imports if not already present (check first with grep)
  - Verify: `grep -n "_cancel_timer\|_max_s.*max_task_seconds" orchid/orchestrator.py` must return at least 2 lines

- [ ] **T218** Create `orchid/watchdog.py`. One class: `TaskWatchdog`. `type:code_generate` `p1` `model:local`
  - Imports: `import logging, threading, time`. `from orchid.session import Session`. `from orchid.memory.state import TaskStatus`
  - `class TaskWatchdog:` with:
  - `__init__(self, session: Session, stuck_threshold_s: int = 1800) -> None` — stores `self._session = session`, `self._threshold = stuck_threshold_s`, `self._in_progress_since: dict[str, float] = {}`, `self._stop = threading.Event()`, `self._thread: threading.Thread | None = None`
  - `start(self) -> None` — creates and starts a daemon thread targeting `self._run` named `"orchid-watchdog"`, stores in `self._thread`
  - `stop(self) -> None` — calls `self._stop.set()`, then if `self._thread` is not None calls `self._thread.join(timeout=5)`
  - `_run(self) -> None` — loops `while not self._stop.wait(60):` calling `self._check()`
  - `_check(self) -> None` — iterates `self._session.tasks`. For each task where `task.status == TaskStatus.IN_PROGRESS`: if `task.id` not in `self._in_progress_since`, add `self._in_progress_since[task.id] = time.monotonic()`. Else if `time.monotonic() - self._in_progress_since[task.id] > self._threshold`: log warning `"[watchdog] Task %s stuck >%ds — marking BLOCKED"`, call `self._session.update_task_status(task.id, TaskStatus.BLOCKED)`, delete from `self._in_progress_since`. For tasks NOT in IN_PROGRESS: remove from `self._in_progress_since` if present.
  - Verify: `grep -n "class TaskWatchdog\|def start\|def stop\|def _run\|def _check" orchid/watchdog.py` must return 5 lines

- [ ] **T219** Extend `orchid/runner.py` — wire `TaskWatchdog` into `_run_loop()`. Read the file first. Find `_run_loop()` at line 184. `type:code_generate` `p1` `model:local` `needs:T218`
  - Add `from orchid.watchdog import TaskWatchdog` to the imports at the top of the file
  - In `_run_loop()`, find where `completed_ids: set[str] = set()` is declared. BEFORE the main scheduler loop, add:
    ```python
    # T219: Start stuck-task watchdog
    _watchdog_threshold = cfg.get("isolation.watchdog_threshold_s", 1800)
    _watchdog = TaskWatchdog(session, stuck_threshold_s=_watchdog_threshold)
    _watchdog.start()
    ```
  - Find the `finally:` block in `_run_loop()` (where mcp and checkpoints are cleaned up). Add `_watchdog.stop()` as the first line of the `finally:` block.
  - Also add `watchdog_threshold_s: 1800` under the `isolation:` block in `orchid/orchid.defaults.yaml` (read the file first, add the line under `isolation:`)
  - Verify: `grep -n "TaskWatchdog\|_watchdog" orchid/runner.py` must return at least 3 lines

- [ ] **T220** Extend `orchid/scheduler.py` — add `has_cycle()` to `DependencyGraph`. Read the file first. Find the `DependencyGraph` class (line 53). Add the method after `get_ready_tasks()`. `type:code_generate` `p1` `model:local`
  - Add this method to `DependencyGraph`:
    ```python
    def has_cycle(self) -> bool:
        """Return True if the dependency graph contains a cycle (DFS)."""
        visited: set[str] = set()
        path: set[str] = set()

        def _dfs(node: str) -> bool:
            visited.add(node)
            path.add(node)
            for dep in self._deps.get(node, set()):
                if dep not in visited:
                    if _dfs(dep):
                        return True
                elif dep in path:
                    return True
            path.discard(node)
            return False

        for node in list(self._deps):
            if node not in visited:
                if _dfs(node):
                    return True
        return False
    ```
  - Also add this exception class at the module level, BEFORE `class DependencyGraph`: `class CyclicDependencyError(Exception): """Raised when inject_task would create a dependency cycle."""`
  - Verify: `grep -n "def has_cycle\|CyclicDependencyError" orchid/scheduler.py` must return 2 lines

- [ ] **T221** Extend `orchid/tools/task_injection.py` — call `has_cycle()` after successful task injection to catch runtime cycles. Read the file first. Find the `inject_task()` function at line 59. `type:code_generate` `p1` `model:local` `needs:T220`
  - Add these imports at the top of the file if not already present: `from orchid.scheduler import DependencyGraph, CyclicDependencyError`
  - In `inject_task()`, after the new task has been appended to `session.tasks` (find where the task is added), add:
    ```python
    # T221: Detect dependency cycles introduced by this injection
    _graph = DependencyGraph(session.tasks)
    if _graph.has_cycle():
        # Roll back the injected task
        session.tasks = [t for t in session.tasks if t.id != new_task_id]
        raise CyclicDependencyError(
            f"inject_task('{title}') would create a dependency cycle — task not added"
        )
    ```
  - Note: `new_task_id` is whatever variable holds the newly created task's ID. Read the existing code to find the right variable name.
  - Verify: `grep -n "has_cycle\|CyclicDependencyError" orchid/tools/task_injection.py` must return 2 lines

- [ ] **T222** Create `tests/test_worker_protocol.py`. Write exactly 4 test functions, no fixtures. `type:code_generate` `p2` `model:local` `needs:T209`
  - `test_taskcontext_to_json_and_from_json()` — create a `TaskContext` with dummy string values, call `to_json()`, call `from_json()` on the result, assert all fields equal the original
  - `test_workerevent_to_json_includes_payload_fields()` — create `WorkerEvent(type="agent_step", task_id="T001", payload={"thought": "hello"})`, call `to_json()`, parse with `json.loads`, assert `result["type"] == "agent_step"` and `result["thought"] == "hello"`
  - `test_workerresult_defaults()` — create `WorkerResult(task_id="T001", success=True)`, assert `result.result == ""` and `result.error == ""` and `result.duration_s == 0.0`
  - `test_workerresult_to_json_roundtrip()` — create `WorkerResult(task_id="T002", success=False, error="oops", duration_s=1.5)`, `json.loads(r.to_json())`, assert `data["success"] is False` and `data["error"] == "oops"`
  - Verify: run `python -m pytest tests/test_worker_protocol.py -q` — all 4 must pass

- [ ] **T223** Create `tests/test_subprocess_runner.py`. Write exactly 3 test functions using `unittest.mock.patch`. `type:code_generate` `p2` `model:local` `needs:T211`
  - `test_run_task_isolated_success()` — patch `subprocess.Popen` to return a mock whose `.stdout` yields two lines: `WorkerEvent(type="agent_step", task_id="T001", payload={"thought":"x"}).to_json()` and `WorkerResult(task_id="T001", success=True, result="done").to_json()`. Patch `.wait()` to return 0. Assert `SubprocessRunner().run_task_isolated(ctx, None, None).success is True`
  - `test_run_task_isolated_calls_stream_callback()` — patch Popen similarly. Collect events via a list. Assert stream_callback was called with the event payload dict.
  - `test_run_task_isolated_timeout_kills_process()` — patch `proc.wait()` to raise `subprocess.TimeoutExpired(cmd="x", timeout=5)`. Assert returned `WorkerResult.success is False` and `"timed out"` in `result.error.lower()`. Assert `proc.kill()` was called.
  - Verify: run `python -m pytest tests/test_subprocess_runner.py -q` — all 3 must pass

- [ ] **T224** Create `tests/test_agent_cancel.py`. Write exactly 3 test functions. `type:code_generate` `p2` `model:local` `needs:T215,T216`
  - `test_cancel_sets_event()` — import `BaseAgent` (or a concrete subclass like `DeveloperAgent`). Create an instance with minimal args (mock project_dir, empty session_context). Call `.cancel()`. Assert `agent._cancel_event.is_set() is True`
  - `test_cancel_event_raises_on_next_iteration()` — create agent, set `agent._cancel_event.set()`. Call `agent.run("dummy task")`. Wrap in `pytest.raises(AgentCancelledError)` — OR if the run catches exceptions internally, assert the return value indicates cancellation.
  - `test_cancel_event_not_set_by_default()` — create agent, assert `agent._cancel_event.is_set() is False`
  - Import `AgentCancelledError` from `orchid.agents.base`
  - Verify: run `python -m pytest tests/test_agent_cancel.py -q` — all 3 must pass

- [ ] **T225** Create `tests/test_watchdog.py`. Write exactly 4 test functions using `tmp_path` and mocks. `type:code_generate` `p2` `model:local` `needs:T218`
  - `test_watchdog_starts_and_stops()` — create a mock Session with `tasks=[]`. Create `TaskWatchdog(session, stuck_threshold_s=60)`. Call `start()` then `stop()`. Assert no exception is raised.
  - `test_watchdog_marks_stuck_task_blocked()` — create mock session with one task whose `status == TaskStatus.IN_PROGRESS`. Set `stuck_threshold_s=0` (so any time is over threshold). Manually call `watchdog._check()` twice (first call records start time; second call with `time.sleep(0.01)` will be over threshold with threshold=0). Assert `session.update_task_status` was called with the task id and `TaskStatus.BLOCKED`.
  - `test_watchdog_does_not_mark_completed_task()` — session has one task with `status == TaskStatus.DONE`. Call `_check()`. Assert `update_task_status` NOT called.
  - `test_watchdog_clears_in_progress_dict_on_completion()` — session has task starting IN_PROGRESS. Call `_check()` to record it. Change task status to DONE. Call `_check()` again. Assert `task.id` not in `watchdog._in_progress_since`.
  - Verify: run `python -m pytest tests/test_watchdog.py -q` — all 4 must pass

- [ ] **T226** Create `tests/test_cycle_detection.py`. Write exactly 3 test functions. `type:code_generate` `p2` `model:local` `needs:T220`
  - Import `DependencyGraph, CyclicDependencyError, Scheduler` from `orchid.scheduler`. Use mock Task objects with `id`, `depends_on`, `rollup_sources`, `status`, `priority` attributes.
  - `test_has_cycle_returns_false_for_acyclic_graph()` — build graph with T1→T2→T3 (T2 depends on T1, T3 depends on T2). Assert `graph.has_cycle() is False`
  - `test_has_cycle_returns_true_for_direct_cycle()` — build graph where T1 depends on T2 AND T2 depends on T1. Assert `graph.has_cycle() is True`
  - `test_has_cycle_returns_true_for_transitive_cycle()` — T1 depends on T2, T2 depends on T3, T3 depends on T1. Assert `graph.has_cycle() is True`
  - Verify: run `python -m pytest tests/test_cycle_detection.py -q` — all 3 must pass

- [ ] **T227** Review Tier 1 implementation (T209-T226). Check: subprocess isolation compiles and is importable, cancellation token raises AgentCancelledError, watchdog marks stuck tasks BLOCKED, cycle detection finds cycles, all new tests pass. `type:review` `p1` `model:claude` `needs:T222,T223,T224,T225,T226`
  - Run `python -c "from orchid.worker_protocol import TaskContext, WorkerEvent, WorkerResult"` — must not error
  - Run `python -c "from orchid.subprocess_runner import SubprocessRunner"` — must not error
  - Run `python -c "from orchid.watchdog import TaskWatchdog"` — must not error
  - Run `python -c "from orchid.scheduler import DependencyGraph; g = DependencyGraph([]); print(g.has_cycle())"` — must print False
  - Run `python -m pytest tests/test_worker_protocol.py tests/test_subprocess_runner.py tests/test_agent_cancel.py tests/test_watchdog.py tests/test_cycle_detection.py -q` — all must pass
  - Report PASS or FAIL for each check with the error message if FAIL

- [ ] **T228** Fix all issues found in T227. Read the T227 result first. Make exactly the fixes listed. `type:code_generate` `p1` `model:local` `needs:T227`

- [ ] **T229** Rollup Tier 1 results `type:rollup` `rollup:T209,T210,T211,T212,T213,T214,T215,T216,T217,T218,T219,T220,T221,T222,T223,T224,T225,T226,T227,T228` `output:TIER1-REPORT.md` `model:claude`
