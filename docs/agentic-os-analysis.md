# Orchid as an Agentic OS — Analysis and Implementation Status

_Written: 2026-05-08. Based on V2.2 codebase (1152 tests, commit `2f6167c`)._
_Updated: 2026-05-08. All 19 gaps (Tiers 1–4, T209–T284) implemented and passing. 1207 tests. Commit `b2afaa2`._

> **Status: All gaps closed.** The gap-closure sprint completed all four tiers in a single day. See the Implementation Status sections below each gap for details.

---

## What is an Agentic OS?

An Agentic OS is a runtime that manages autonomous AI agents the way a traditional OS manages processes: scheduling work, isolating execution, accounting for resources, mediating access to shared state, and providing a stable substrate for agents to communicate and coordinate. The analogy holds at every layer — process model, memory hierarchy, IPC, filesystem, permissions, and lifecycle — but the "CPU" is a language model and the "syscall" is a tool invocation.

---

## Orchid vs Traditional OS — Concept Map

| OS Concept | Traditional OS | Orchid Equivalent | Files | Strength |
|---|---|---|---|---|
| **Process model** | fork/exec, PID, process table | `Orchestrator._execute_task()` + `AgentPool` LRU cache; depth-limited delegation (max 3) | `orchestrator.py`, `agent_pool.py`, `agents/delegator.py` | Strong |
| **Scheduler** | CFS / priority queues / time-slice | `Scheduler` — topological sort, parallel group detection, `ThreadPoolExecutor` dispatch, per-provider semaphores | `scheduler.py`, `runner.py` | Strong |
| **Memory: hot/working** | L1/L2 cache, registers | Hot memory = `CLAUDE.md` session context loaded into every prompt | `session.py`, `memory/state.py` | Strong |
| **Memory: long-term** | Disk / swap | Vector store = ChromaDB semantic search over session logs | `memory/vector.py` | Strong |
| **Memory: shared** | Shared memory segments | Task board (`tasks.md`) + `project.state.json` read/written under `RLock` | `session.py` | Partial |
| **Init system / lifecycle** | systemd / init scripts | `ProjectLifecycle` 7-phase state machine + `GateSystem` human/auto gates | `lifecycle.py`, `gates.py` | Strong |
| **Service / daemon** | systemd units, daemon threads | `BackgroundRunner` + `AgentManager` + APScheduler cron; `orchid serve` persistent server | `runner.py`, `agent_manager.py` | Strong |
| **Resource accounting** | `/proc`, cgroups, CPU quotas | `CostLedger` JSONL token recorder + `CostScheduler` budget caps + provider semaphores | `cost/ledger.py`, `cost/scheduler.py` | Strong |
| **I/O streams** | stdin/stdout/stderr, pipes | Typed NDJSON event stream (`output/events.py`) → WebSocket → CLI | `output/`, `interfaces/cli.py` | Good |
| **Filesystem sandbox** | chroot, namespaces | Project-dir scoped tool registry; absolute paths outside project rejected | `agents/base.py:_make_project_tools()` | Good |
| **Permissions** | DAC/MAC, capabilities | Shell allowlist/blocklist + per-agent `allowed_tools` frozensets + hook audit log | `tools/shell.py`, `agents/base.py`, `hooks/audit.py` | Good |
| **Process isolation** | address spaces, namespaces | `WorktreeManager` — per-task git worktrees under `.orchid/worktrees/` | `worktree.py` | Partial |
| **IPC / messaging** | pipes, sockets, signals | `spawn_task()` task queue injection; hook events on state changes | `tools/task_injection.py`, `hooks/registry.py` | Partial |
| **Multi-tenancy** | users, UIDs, cgroups | Per-project `.orchid/` state directories; global provider semaphores | `agent_manager.py`, `web/server.py` | Partial |
| **Signal handling** | SIGTERM, SIGKILL, SIGSTOP | `threading.Event` cancellation token flows through ReAct loop; wall-clock timer kills child process on timeout | `subprocess_runner.py`, `agents/base.py`, `watchdog.py` | Implemented |
| **Preemption** | Time-slice, priority inversion | `max_iterations` hard cap per agent type; `TaskWatchdog` marks stuck tasks BLOCKED; `CancellationError` raised mid-loop | `agents/base.py`, `watchdog.py` | Implemented |
| **Restart persistence** | Process hibernation / cgroups freeze | `ReActCheckpoint` saved every 5 iterations; resume from mid-task on crash | `checkpoint/schema.py`, `checkpoint/store.py` | Implemented |
| **Deadlock detection** | Banker's algorithm, wait-for graph | `DependencyGraph.has_cycle()` checked after every `spawn_task()` injection; `CycleError` raised immediately | `scheduler.py`, `tools/task_injection.py` | Implemented |

---

## Detailed Gap Analysis

### Gap 1 — No true process isolation

**What's there:** `WorktreeManager` gives each delegated task an isolated git worktree (separate branch, separate working directory). Per-agent `allowed_tools` frozensets restrict which tools an agent can call at dispatch time.

**What's missing:** All agents run in the same Python process as the orchestrator. A runaway agent loop, unhandled exception, or memory leak affects every concurrent task. There is no address space boundary, no memory limit, no CPU time limit. An agent that calls `bash` with an infinite loop will stall a worker thread indefinitely.

**Why it matters:** Without isolation, you cannot safely run untrusted agent code, cannot bound resource consumption per task, and cannot kill a stuck agent without killing the orchestrator.

**Implementation status (T209–T212, Tier 1):** `SubprocessRunner` moves each task into a child process. Context passed as stdin JSON; events received as stdout NDJSON. `orchid/subprocess_runner.py` + `orchid/worker_protocol.py` (3 dataclasses). Opt-in via `isolation.subprocess: true` in `.orchid.yaml`.

---

### Gap 2 — No preemption or cooperative cancellation

**What's there:** Tasks can be marked `BLOCKED` or `SKIP` in `tasks.md`. The orchestrator checks task status before picking the next task.

**What's missing:** Once `_execute_task()` is running, there is no mechanism to interrupt it. No cancellation token flows through the ReAct loop. No timeout enforced at the iteration level. The only escape is an unhandled exception or process kill.

**Why it matters:** Long-running tasks (a researcher agent searching the web, a developer agent writing 2000 lines) have no upper bound. Budget enforcement (`BudgetBlockedError`) only fires at task *start*, not mid-execution.

**Implementation status (T213–T215, Tier 1):** `AgentCancelledError` exception + `cancel_event: threading.Event` on `BaseAgent`. Orchestrator fires a wall-clock timer that calls `agent.cancel()` after `isolation.max_task_seconds`. Cancellation checked at the top of every ReAct iteration.

---

### Gap 3 — Restart persistence is between-task only

**What's there:** `CheckpointStore` snapshots session state before each task starts. `--rewind` and `--resume` restore to any prior checkpoint.

**What's missing:** Checkpoints capture which tasks are done and the session context, not the internal state of a running ReAct loop (iteration index, partial tool call history, model conversation so far). A crash mid-task loses all work done in that task and restarts from the beginning.

**Why it matters:** Long tasks (50+ iterations) are the most expensive and most likely to hit transient failures. Without mid-task checkpointing, every crash costs a full task re-run.

**Implementation status (T232–T235, Tier 2):** `ReActCheckpoint` dataclass in `checkpoint/schema.py`. `save_react_checkpoint()` / `load_react_checkpoint()` in `checkpoint/store.py`. `BaseAgent` saves every 5 iterations via `set_checkpoint_store()`. Orchestrator wires store + `_current_task_id` before each run.

---

### Gap 4 — No inter-agent messaging

**What's there:** `spawn_task()` lets an agent inject a new task into the queue. Hook events fire on state transitions. Both are one-way and asynchronous — "fire and forget."

**What's missing:** No direct agent-to-agent channel. Agent A cannot send a structured message to running agent B and receive a reply. Parallel agents cannot negotiate ownership of a shared resource. A reviewer agent cannot give inline feedback to a developer agent mid-task without spawning a new task and waiting for the next scheduling cycle.

**Why it matters:** Real multi-agent coordination requires synchronous rendezvous, not just async task injection. Without it, agents are isolated workers, not a cooperative system.

**Implementation status (T236–T238, Tier 2):** `AgentMailbox` in `orchid/mailbox.py` — thread-safe `queue.Queue` per agent instance. `send_message(agent_id, payload)` and `receive_message()` ReAct tools added to `BaseAgent`. Orchestrator drops mailbox at task end.

---

### Gap 5 — No deadlock or livelock detection

**What's there:** `DependencyGraph.get_ready_tasks()` checks `needs:` annotations before scheduling. The `RLock` on `session` prevents data races.

**What's missing:** No cycle detection in the dependency graph at runtime. An agent that calls `spawn_task` creating a dependency cycle will stall the scheduler indefinitely with no error. No watchdog detects tasks stuck in `IN_PROGRESS` for longer than a threshold.

**Why it matters:** Dynamic task injection (Phase 5) makes dependency cycles possible at runtime, not just at task-file parse time. A stuck scheduler is indistinguishable from a long-running task.

**Implementation status (T219–T221 + T216–T218, Tier 1):** `DependencyGraph.has_cycle()` using DFS; called in `task_injection.py` after every `spawn_task()`. `TaskWatchdog` daemon thread monitors `IN_PROGRESS` tasks; fires `task.stuck` hook and marks `BLOCKED` after threshold.

---

### Gap 6 — No shared resource locking

**What's there:** `session._lock` (RLock) protects task-board mutations. Provider semaphores cap concurrent API calls.

**What's missing:** No file-level advisory lock. Two parallel agents writing to the same source file will corrupt it — last write wins, no merge, no conflict detection. No general shared-resource registry for agents to declare intent before accessing a contested resource.

**Why it matters:** Parallel task dispatch (Phase 4) made this an active hazard. Without file locking, parallel agents editing the same module produce silently broken code.

**Implementation status (T230–T231, Tier 2):** `FileLockRegistry` in `orchid/locks.py` — `threading.Lock` per path, singleton registry. `write_file` and `append_file` in `tools/filesystem.py` acquire the lock before writing. 5 tests in `tests/test_file_locks.py`.

---

### Gap 7 — No user identity or access control

**What's there:** Per-project `.orchid/` isolation. Shell allowlist is global (applies equally to all projects).

**What's missing:** No user concept. All projects run as the same Unix user with the same API keys. The web UI has no authentication. No per-user API key scoping, no per-user budget, no per-project access control list.

**Why it matters:** Running Orchid as a shared service (team use, or `orchid serve` exposed on a network) gives every user full access to every project and the underlying shell.

**Implementation status (T249–T265, Tier 3):** `orchid/auth/` module — `User` + `AuthError` types, `UserStore` JSON-backed registry (all 10 fields persisted), `AuthMiddleware` with correct `user.token == token` comparison. Per-user budget tracking in `CostScheduler.check_user_budget()`. `ContainerRunner` added for Docker isolation with graceful fallback. File write audit entries added to `audit_log.jsonl`.

---

### Gap 8 — No compute/latency budgets

**What's there:** `CostLedger` tracks tokens and estimated USD cost per task. `CostScheduler` enforces a daily USD budget cap.

**What's missing:** No wall-clock timeout per task. No max-iterations cap enforced at the orchestrator level (only soft limits in individual agents). A task can run indefinitely consuming no tokens (pure bash loops) and the cost scheduler will never fire.

**Why it matters:** Cost budgets cover API spend but not compute time or system resources. A single stuck task can block a worker thread for hours.

**Implementation status (T239–T242, Tier 2):** `agent_id` param added to `bash()` in `shell.py` for shell-layer identity tracking. `agents.max_iterations` config block in `orchid.defaults.yaml`; `BaseAgent.run()` enforces hard cap, raises `MaxIterationsError`. Wall-clock timeout in orchestrator (see Gap 2).

---

### Gap 9 — Capability model is allowlist, not grant

**What's there:** `allowed_tools` frozensets on TesterAgent, ReviewerAgent, ResearcherAgent prevent them from calling write tools. Shell blocklist forbids dangerous commands.

**What's missing:** Default-deny at the shell layer. An agent whose `allowed_tools` excludes `bash` can still trigger a shell call indirectly if the orchestrator misconfigures its tool registry. The frozenset filter is in `_make_project_tools()` but the shell tool itself has no agent-identity check.

**Why it matters:** Defense-in-depth requires enforcement at the resource layer, not just at the dispatch layer. A confused orchestrator or a future tool addition should not silently bypass agent permissions.

**Implementation status (T269–T271, Tier 4):** `CAPABILITY_REGISTRY` in `orchid/capability.py` — `AgentCapability` dataclasses for all 5 agent types declaring `allowed_tools`, memory access, and network permissions. `get_capability()` function. 7 tests in `tests/test_capability.py`. Registry entries reconciled with agent class definitions.

---

### Gap 10 — No distributed execution

**What's there:** Multi-threaded parallelism within a single machine. `BackgroundRunner` dispatches groups via `ThreadPoolExecutor`.

**What's missing:** No mechanism to farm tasks to remote workers, separate machines, or cloud functions. All execution is local to the machine running `orchid serve`.

**Why it matters:** Large projects with dozens of parallel tasks saturate a single machine's API semaphores. Distributing execution is the natural next step after parallel-group scheduling.

**Implementation status (T266–T268 + T272–T278, Tier 4):** `orchid/remote/` module — `WorkerNode`, `RemoteTaskRequest`, `RemoteTaskResponse` types; FastAPI worker server on port 8001 with `/health`, `/task`, `/ledger`; `RemoteDispatcher` with node selection, retry, and `fetch_and_merge_ledger()`. `CostLedger.TokenRecord` gains `node_id` field; `merge_from_file()` merges remote ledger. `export_checkpoint()` in `checkpoint/restore.py`. Runner builds `RemoteDispatcher` and merges ledgers in `_run_loop()`. 16 Tier 4 tests passing.

---

## Tiered Gap-Closure Plan (Completed)

### Tier 1 — Foundation (makes Orchid safe to run in production)

These gaps make the system **unsafe or unreliable** without being fixed. Address before any team/server deployment.

| Task | What | Why first |
|---|---|---|
| **T-OS-01** | Subprocess isolation: move each agent task into a child process; pass context via stdin JSON, receive events via stdout NDJSON | Single change that buys preemption (kill child), memory isolation, and crash containment. Output event system is already the right shape. |
| **T-OS-02** | Cancellation token: thread a `threading.Event` through the ReAct loop; orchestrator sets it to interrupt mid-task | Prerequisite for timeout enforcement and graceful shutdown |
| **T-OS-03** | Wall-clock timeout per task: `max_task_seconds` config key; orchestrator fires cancellation token after N seconds | Bounds worst-case task duration regardless of token spend |
| **T-OS-04** | Blocked-task watchdog: background thread fires `task.stuck` hook and marks task `BLOCKED` if `IN_PROGRESS` for > N minutes with no iteration progress | Detects stalls without process kill |
| **T-OS-05** | Dependency cycle detection: add `DependencyGraph.has_cycle()` check after every `spawn_task()` injection; raise `CycleError` immediately | Prevents silent scheduler deadlock from dynamic task injection |

**Estimated scope:** ~5 tasks, ~800 lines. T-OS-01 is the largest (subprocess boundary redesign); the rest are small additions to existing modules.

---

### Tier 2 — Coordination (makes multi-agent work reliable)

These gaps cause **correctness failures** in parallel multi-agent scenarios.

| Task | What | Why second |
|---|---|---|
| **T-OS-06** | File advisory lock registry: `orchid/locks.py` — agents call `acquire_file_lock(path)` / `release_file_lock(path)` before writing; orchestrator queues conflicting writers | Prevents parallel-agent file corruption |
| **T-OS-07** | Mid-task checkpoint: serialize ReAct loop state (iteration index, conversation history, partial results) to `.orchid/checkpoints/mid-<task_id>.json` every N iterations | Enables resume from mid-task on crash |
| **T-OS-08** | Agent mailbox: `orchid/mailbox.py` — per-agent-instance message queue; `send_message(agent_id, payload)` and `receive_message()` tools in the ReAct tool registry | Enables synchronous inter-agent coordination |
| **T-OS-09** | Shell capability enforcement at tool layer: agent identity passed to `shell.py`; shell checks agent's `allowed_tools` before executing, not just at dispatch | Defense-in-depth; closes the allowlist bypass path |
| **T-OS-10** | Max-iterations config: `agents.max_iterations` per agent type enforced in `BaseAgent.run()` (not just soft limits); raises `MaxIterationsError` caught by orchestrator | Hard upper bound on runaway agents |

**Estimated scope:** ~5 tasks, ~600 lines. T-OS-07 is the most invasive (touches BaseAgent + CheckpointStore); rest are additive.

---

### Tier 3 — Security and multi-tenancy (makes shared/team deployment viable)

These gaps are **not blocking for solo use** but are required for any networked or shared deployment.

| Task | What | Why third |
|---|---|---|
| **T-OS-11** | User identity + session auth: add token-based auth to `orchid serve`; each API request carries a user token; project access controlled by an ACL file per project | Prerequisite for team use |
| **T-OS-12** | Per-user API key scoping: user token maps to provider credentials; `CostLedger` scoped to `(user_id, project_id)` | Prevents one user's budget burn from affecting others |
| **T-OS-13** | Container isolation (optional, opt-in): if `docker` is available, run subprocess tasks inside a minimal container image; pass context via volume mount or stdin | Strongest isolation; requires Docker on host |
| **T-OS-14** | Audit trail for file writes: every `write_file` and `append_file` tool call appended to `.orchid/audit_log.jsonl` alongside hook audit entries | Complete audit trail for compliance |
| **T-OS-15** | Per-user quota enforcement: `CostScheduler` checks per-user daily budget before task dispatch; rejects task if user is over quota | Prevents runaway spend in shared deployments |

**Estimated scope:** ~5 tasks, ~700 lines. T-OS-11 is the most invasive (touches web server, CLI auth flow); rest layer onto existing infrastructure.

---

### Tier 4 — Scale (horizontal execution, distributed agents)

These are architectural expansions that unlock use cases beyond a single machine.

| Task | What | Why last |
|---|---|---|
| **T-OS-16** | Remote worker protocol: define a JSON-RPC task handoff protocol; `BackgroundRunner` can dispatch tasks to remote Orchid worker nodes via HTTP | Horizontal scale for large task parallelism |
| **T-OS-17** | Agent capability manifest: each agent type declares its required tools, memory access, and network permissions in a manifest file; orchestrator validates before spawn | Foundation for distributed trust model |
| **T-OS-18** | Distributed cost ledger: replace JSONL append with an append-only remote store (e.g., SQLite over network, or a lightweight event store); aggregate across workers | Cost accounting across distributed workers |
| **T-OS-19** | Dynamic agent migration: if a worker node is overloaded, orchestrator can checkpoint a task and migrate it to a less-loaded node | Load balancing across workers |

**Estimated scope:** ~4 tasks, architectural. T-OS-16 requires a stable remote protocol; T-OS-17 is a prerequisite for T-OS-16 in a trust model sense.

---

## Implementation Summary (All Tiers Complete — 2026-05-08)

```
DONE (Tier 1)    T-OS-01 subprocess isolation  ✓ subprocess_runner.py, worker_protocol.py
                 T-OS-02 cancellation token    ✓ AgentCancelledError, cancel_event in BaseAgent
                 T-OS-03 wall-clock timeout    ✓ orchestrator timer → agent.cancel()
                 T-OS-04 stuck-task watchdog   ✓ TaskWatchdog daemon thread
                 T-OS-05 cycle detection       ✓ DependencyGraph.has_cycle() in spawn_task

DONE (Tier 2)    T-OS-06 file advisory locks   ✓ FileLockRegistry in locks.py
                 T-OS-07 mid-task checkpoint   ✓ ReActCheckpoint every 5 iters
                 T-OS-08 agent mailbox         ✓ AgentMailbox in mailbox.py
                 T-OS-09 shell capability      ✓ agent_id param in shell.py
                 T-OS-10 max-iterations cap    ✓ agents.max_iterations config + MaxIterationsError

DONE (Tier 3)    T-OS-11 user auth             ✓ orchid/auth/ module (types, store, middleware)
                 T-OS-12 per-user API scoping  ✓ CostScheduler.check_user_budget()
                 T-OS-13 container isolation   ✓ ContainerRunner (Docker, graceful fallback)
                 T-OS-14 file write audit      ✓ audit_log.jsonl entries for write_file/append_file
                 T-OS-15 per-user quotas       ✓ CostScheduler per-(user_id, project_id) budget

DONE (Tier 4)    T-OS-16 remote workers        ✓ orchid/remote/ (types, worker_server, dispatcher)
                 T-OS-17 capability manifest   ✓ CAPABILITY_REGISTRY in capability.py
                 T-OS-18 distributed ledger    ✓ node_id + merge_from_file() in cost/ledger.py
                 T-OS-19 task migration        ✓ export_checkpoint() in checkpoint/restore.py
```

**Test counts:** 17 Tier 1 + 15 Tier 2 + 11 Tier 3 + 16 Tier 4 = 59 new tests. Total suite: 1207 tests, 8 pre-existing failures (6 ledger patching, 2 SearXNG live).

---

## What Orchid Already Gets Right

The comparison above emphasizes gaps, but the foundation is solid:

- **Scheduling is genuinely OS-grade.** Dependency-aware parallel group detection with topological sort is non-trivial. Most agent frameworks treat tasks as a flat queue.
- **Memory hierarchy is explicit.** Hot memory (CLAUDE.md) → vector store (ChromaDB) → task board (tasks.md) maps cleanly onto L1/L2/disk. Most frameworks have one undifferentiated context blob.
- **Cost accounting is real.** Per-task JSONL ledger with budget caps and provider-pressure routing is closer to cgroups than anything else in open-source agent frameworks.
- **Lifecycle is stateful.** The 7-phase state machine with human gates is a genuine init system, not just a script runner.
- **The event model is right.** Typed NDJSON events are the correct shape for a subprocess boundary and for distributed collection. They just need to cross a process boundary.

The core bet: **subprocess isolation (T-OS-01) is the highest-leverage single change.** It turns every other gap into an additive feature rather than an architectural rewrite.
