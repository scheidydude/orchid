# Agentic OS Gap Closure Plan

Closes the six remaining gaps identified in `docs/Orchid vs Agentic OS.md`,
ordered high → low priority. Each phase is independently shippable.

---

## Priority rationale

| # | Gap | Priority | Why |
|---|-----|----------|-----|
| 1 | Signal handling / graceful shutdown | **P0** | Can't safely deploy, restart, or cancel without it. Foundation for everything below. |
| 2 | Restart persistence (auto-resume) | **P0** | ReAct checkpoints already exist — just needs wiring. High value, low risk. |
| 3 | True sandbox (always-on subprocess) | **P1** | Security boundary for multi-user / enterprise. Opt-in today is not enough. |
| 4 | Preemption (pause / resume / priority) | **P2** | Enables budget pressure response, human override, priority inversion. |
| 5 | Backpressure (emitter buffering) | **P2** | Prevents slow consumers stalling the agent event loop under load. |
| 6 | CPU / latency budgets | **P3** | Wall-clock caps beyond `max_task_seconds`; needed for hard SLA enforcement. |

---

## Phase 1 — Graceful shutdown + SIGTERM handling (P0)

**Goal:** `orchid serve` and background runners handle SIGTERM/SIGINT without
leaving tasks `IN_PROGRESS` or corrupting state files.

### What to build

**1.1 — CancellationToken propagation**

- `CancellationToken` (threading.Event) already exists in-process but is not
  wired to OS signals.
- Register `signal.signal(SIGTERM, _handler)` in `serve()` and in
  `SubprocessRunner` parent.
- `_handler` sets the global cancellation token and logs the signal source.

**1.2 — Agent loop check**

- `BaseAgent.run()` already has a cancellation path — ensure it is checked at
  the top of every ReAct iteration (not just tool boundaries).
- On cancellation: save a mid-task ReAct checkpoint, set task status to
  `BLOCKED` with reason `"cancelled:sigterm"`, flush cost ledger.

**1.3 — Graceful drain in `serve()`**

- On SIGTERM: stop accepting new task dispatches, wait up to `shutdown_timeout`
  (default 30 s) for in-flight tasks to finish or checkpoint, then exit.
- Expose `shutdown_timeout` in `orchid.defaults.yaml`.

**1.4 — systemd `KillMode=mixed` + `TimeoutStopSec`**

- Update `scripts/orchid-serve.service` to send SIGTERM first, then SIGKILL
  after `TimeoutStopSec=35` (5 s buffer over shutdown_timeout).

### Files to change

| File | Change |
|------|--------|
| `orchid/runner.py` | Hook SIGTERM → set cancellation token |
| `orchid/agents/base.py` | Check token at iteration start, checkpoint on cancel |
| `orchid/interfaces/web_server.py` | Lifespan shutdown: drain in-flight tasks |
| `orchid/subprocess_runner.py` | Forward SIGTERM to child PID |
| `scripts/orchid-serve.service` | `KillMode=mixed`, `TimeoutStopSec=35` |
| `orchid/orchid.defaults.yaml` | `runner.shutdown_timeout: 30` |

### Done when

- `sudo systemctl stop orchid-serve` returns within 35 s.
- Running task status is `BLOCKED` (not `IN_PROGRESS`) after stop.
- No partial writes to `users.json` or `tasks.md`.

---

## Phase 2 — Restart persistence / auto-resume (P0)

**Goal:** tasks interrupted by a crash or restart automatically resume from
their most recent ReAct checkpoint rather than re-running from scratch.

### What to build

**2.1 — Startup scan for orphaned IN_PROGRESS tasks**

- On `AgentManager` / `BackgroundRunner` start, scan all registered projects
  for tasks with status `IN_PROGRESS`.
- These are tasks that were running when the previous process died.

**2.2 — Resume from ReAct checkpoint**

- For each orphaned task: check for a mid-task checkpoint at
  `.orchid/checkpoints/mid-<task_id>.json`.
- If checkpoint exists and is < `max_checkpoint_age` (default 24 h): resume
  `BaseAgent.run()` from the saved ReAct iteration.
- If no checkpoint or too old: reset task to `TODO` and re-run from scratch.

**2.3 — Crash guard file**

- Write `.orchid/running.pid` on runner start; remove it on clean shutdown.
- If `.orchid/running.pid` exists on startup (previous crash), trigger orphan
  scan automatically.

**2.4 — CLI flag**

```bash
orchid --project PATH --recover   # manual trigger of orphan scan + resume
```

**2.5 — Web UI indicator**

- If a project has orphaned tasks at startup, show a "Recovering N tasks…"
  banner in the Task Board tab until resume completes.

### Files to change

| File | Change |
|------|--------|
| `orchid/runner.py` | Write/remove PID file; orphan scan on start |
| `orchid/checkpoint/restore.py` | `resume_orphaned_tasks(project_path)` |
| `orchid/agents/base.py` | Accept `start_from_iteration=N` param |
| `orchid/interfaces/cli.py` | `--recover` flag |
| `orchid/interfaces/web_server.py` | Expose recovery status on project endpoint |
| `orchid/interfaces/web_ui/src/components/TaskBoard.jsx` | Recovery banner |

### Done when

- Kill `orchid serve -9`, restart; in-progress tasks resume from last
  checkpoint without user intervention.
- `orchid --project PATH --recover` works from CLI.

---

## Phase 3 — Always-on subprocess isolation (P1)

**Goal:** every task runs in a child process by default (not opt-in). The main
process is a supervisor only — no agent code runs in it.

### What to build

**3.1 — Flip the default**

- Change `isolation.subprocess` default from `false` → `true` in
  `orchid.defaults.yaml`.
- Add `isolation.subprocess_fallback: true` — if child fails to start, fall
  back to in-process with a warning log.

**3.2 — Subprocess startup latency reduction**

- Current `SubprocessRunner` forks a new Python interpreter per task.
- Add a **worker pool**: pre-fork N idle worker processes that each wait for a
  task over stdin. Eliminates interpreter startup overhead (~0.3–0.8 s).
- Pool size configured by `runner.subprocess_workers` (default: match
  `max_parallel`).

**3.3 — Resource limits per child**

- After fork, apply `resource.setrlimit()`:
  - `RLIMIT_AS` — address space cap (default: 4 GB)
  - `RLIMIT_CPU` — CPU seconds cap (default: `max_task_seconds * 2`)
  - `RLIMIT_NOFILE` — open file limit (default: 256)
- Expose overrides in `.orchid.yaml` under `isolation.resource_limits`.

**3.4 — Namespace isolation (Linux only, optional)**

- If `isolation.namespace: true` and running Linux: use `unshare` to give
  each child its own network and mount namespace.
- Graceful skip on macOS / non-Linux.

### Files to change

| File | Change |
|------|--------|
| `orchid/orchid.defaults.yaml` | `isolation.subprocess: true` |
| `orchid/subprocess_runner.py` | Pre-fork worker pool; resource limits; namespace opt-in |
| `orchid/worker_subprocess.py` | Worker event loop (wait for task, execute, return) |
| `orchid/config.py` | Expose `isolation.resource_limits` config block |

### Done when

- `orchid --project PATH --mode auto` runs tasks in child processes by default.
- A segfault or OOM in a task child doesn't crash the orchestrator.
- Worker pool reuse is measurable: second task starts in < 50 ms.

---

## Phase 4 — Preemption: pause / resume / priority override (P2)

**Goal:** a running task can be paused (suspended) in favour of a
higher-priority task, then resumed without data loss.

### What to build

**4.1 — Suspend signal**

- `CancellationToken` gains a second state: `SUSPEND` (vs `CANCEL`).
- On `SUSPEND`: agent saves a mid-task ReAct checkpoint and parks in a
  `threading.Event.wait()` loop (no CPU burn).
- On `RESUME`: agent reloads checkpoint and continues from next iteration.

**4.2 — Priority queue in scheduler**

- `DependencyGraph` dispatch assigns each ready task a numeric priority score
  (p1=10, p2=5, p3=1, plus age bonus: +1 per minute waiting).
- `Scheduler.next_batch()` returns the highest-priority tasks that fit in
  `max_parallel`, suspending lower-priority running tasks if a higher-priority
  task becomes unblocked.

**4.3 — API + Web UI controls**

```
POST /api/projects/{id}/tasks/{task_id}/suspend
POST /api/projects/{id}/tasks/{task_id}/resume
```

- Web UI: add Pause ⏸ / Resume ▶ buttons next to running tasks in Task Board.

**4.4 — Preemption budget**

- Config: `runner.preemption_enabled: false` (opt-in, off by default).
- Config: `runner.preemption_min_runtime_s: 30` — don't preempt a task that
  started less than 30 s ago (avoids thrashing).

### Files to change

| File | Change |
|------|--------|
| `orchid/scheduler.py` | Priority-weighted dispatch, preemption logic |
| `orchid/agents/base.py` | SUSPEND state handling; checkpoint-and-park |
| `orchid/runner.py` | `suspend_task()`, `resume_task()` |
| `orchid/interfaces/web_server.py` | `/suspend`, `/resume` endpoints |
| `orchid/interfaces/web_ui/src/components/TaskBoard.jsx` | Pause/Resume buttons |
| `orchid/orchid.defaults.yaml` | `runner.preemption_*` config |

### Done when

- High-priority task creation preempts a running low-priority task within one
  ReAct iteration.
- Suspended task resumes exactly where it left off.
- No task state is lost through a suspend/resume cycle.

---

## Phase 5 — Backpressure: bounded emitter buffers (P2)

**Goal:** slow WebSocket / SSE consumers cannot stall the agent event loop.

### What to build

**5.1 — Bounded deque in NDJSONStreamEmitter**

- Replace `deque()` (unbounded) with `deque(maxlen=MAX_BUFFER)`.
- `MAX_BUFFER` default: 1000 events (configurable via `web.emitter_buffer`).
- When full, oldest events are silently dropped (ring-buffer semantics).
  Log a warning when the buffer first fills.

**5.2 — Slow-consumer detection**

- Track `last_drain_at` timestamp per emitter.
- If an emitter hasn't been drained in > `web.slow_consumer_timeout` (default
  30 s): log a warning and mark the emitter `SLOW`.
- `SLOW` emitters skip non-critical events (agent_thought, tool_result) and
  only emit task_start, task_complete, session_end.

**5.3 — WebSocket send timeout**

- Wrap `await ws.send_json(...)` with `asyncio.wait_for(timeout=5.0)`.
- On timeout: close the WebSocket gracefully with code 1001 (Going Away).

**5.4 — SSE heartbeat**

- SSE stream (`/api/projects/{id}/stream/sse`) sends a `: heartbeat\n\n`
  comment every 15 s to detect dead connections early.

### Files to change

| File | Change |
|------|--------|
| `orchid/web/server.py` | Bounded deque, slow-consumer detection |
| `orchid/interfaces/web_server.py` | WebSocket send timeout; SSE heartbeat |
| `orchid/output/ndjson_emitter.py` | `maxlen` param, `SLOW` state |
| `orchid/orchid.defaults.yaml` | `web.emitter_buffer`, `web.slow_consumer_timeout` |

### Done when

- A WebSocket client that stops reading does not stall an agent run.
- Buffer full warnings appear in logs before events are dropped.
- Dead WebSocket connections are closed within 5 s of going silent.

---

## Phase 6 — CPU / wall-clock / latency budgets (P3)

**Goal:** enforce hard per-task wall-clock, CPU, and iteration-latency limits
beyond the existing `max_task_seconds` timeout.

### What to build

**6.1 — Per-task latency budget**

- Track time spent in each ReAct iteration.
- If a single iteration exceeds `agents.max_iteration_seconds` (default: 120):
  log a warning. After 3 consecutive slow iterations: cancel the task and mark
  BLOCKED with reason `"latency_budget_exceeded"`.

**6.2 — CPU time accounting**

- In subprocess mode: read `/proc/<pid>/stat` (Linux) or
  `resource.getrusage(RUSAGE_CHILDREN)` after each task.
- Accumulate `cpu_seconds` in the cost ledger alongside token cost.
- Emit `cpu_seconds` in `task_complete` NDJSON event.

**6.3 — Per-user CPU quota**

- `User.cpu_budget_seconds` field (default: 0 = unlimited).
- `CostScheduler.check_cpu_budget(user)` — raise `BudgetBlockedError` if
  user has exceeded their daily CPU quota.
- Admin endpoint: `PUT /api/auth/users/{id}` already accepts arbitrary fields
  — add `cpu_budget_seconds` to the schema.

**6.4 — Dashboard display**

- PM Dashboard: add CPU seconds column to TaskTiming table.
- Settings tab: show per-user CPU quota and daily usage.

### Files to change

| File | Change |
|------|--------|
| `orchid/agents/base.py` | Per-iteration latency tracking |
| `orchid/subprocess_runner.py` | CPU accounting after child exit |
| `orchid/cost/ledger.py` | `cpu_seconds` field in ledger entries |
| `orchid/cost/scheduler.py` | `check_cpu_budget(user)` |
| `orchid/auth/types.py` | `User.cpu_budget_seconds` field |
| `orchid/interfaces/web_server.py` | `cpu_budget_seconds` in user update endpoint |
| `orchid/interfaces/web_ui/src/components/pm/PMDashboard.jsx` | CPU column |
| `orchid/orchid.defaults.yaml` | `agents.max_iteration_seconds: 120` |

### Done when

- A runaway agent that exceeds its iteration latency budget is cancelled
  automatically.
- CPU seconds appear in the cost ledger and PM dashboard.
- Admin can set per-user CPU quotas via the web UI.

---

## Summary

| Phase | Gap closed | Effort | Shipped as |
|-------|-----------|--------|------------|
| 1 | Signal handling / graceful shutdown | S (2–3 days) | V2.4 |
| 2 | Restart persistence / auto-resume | S (2–3 days) | V2.4 |
| 3 | Always-on subprocess isolation | M (1 week) | V2.5 |
| 4 | Preemption: pause / resume | M (1 week) | V2.6 |
| 5 | Backpressure: bounded emitter | S (1–2 days) | V2.6 |
| 6 | CPU / latency budgets | M (1 week) | V2.7 |

Phases 1 and 2 are both P0 and can be developed in parallel — they share no
files. Ship them together as V2.4. Phases 3–6 each gate on the previous
phase's subprocess/isolation primitives being in place.
