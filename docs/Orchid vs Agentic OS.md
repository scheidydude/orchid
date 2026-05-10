Orchid vs Agentic OS

  Strong analogs (Orchid has real implementations):

  ┌────────────────────┬──────────────────────────────────────────────────────────────────────────────────┬──────────┐
  │     OS Concept     │                                Orchid Equivalent                                 │ Strength │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────┤
  │ Scheduler          │ Scheduler + ParallelGroupDetector (scheduler.py) — topological sort, parallel    │ Strong   │
  │                    │ groups, priority ordering, ThreadPoolExecutor dispatch                           │          │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────┤
  │ Process model      │ Orchestrator._execute_task() + AgentPool — hierarchical, depth-limited to 3      │ Strong   │
  │                    │ levels, pool-cached agent instances                                              │          │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────┤
  │ Init system /      │ ProjectLifecycle 7-phase state machine + GateSystem human/auto gates             │ Strong   │
  │ lifecycle          │ (lifecycle.py, gates.py)                                                         │          │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────┤
  │ Memory tiers       │ Hot memory (CLAUDE.md) + vector store (ChromaDB) + shared task board (tasks.md)  │ Strong   │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────┤
  │ Resource           │ CostLedger + CostScheduler — per-task token/cost JSONL, budget caps, provider    │ Strong   │
  │ accounting         │ semaphores                                                                       │          │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────┤
  │ Service/daemon     │ BackgroundRunner + AgentManager + APScheduler cron — multi-project concurrent    │ Strong   │
  │                    │ background threads                                                               │          │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────┤
  │ Multi-tenancy /    │ V2.3: JWT auth, argon2id passwords, role-based access (user/admin/readonly),     │ Strong   │
  │ Access control     │ per-user project scoping, API key scopes, OAuth/SSO, append-only audit log,      │          │
  │                    │ pluggable store (file or PostgreSQL for shared multi-node deployments)            │          │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────┤
  │ Permissions        │ Shell allowlist/blocklist + per-agent allowed_tools frozensets + JWT role        │ Strong   │
  │                    │ enforcement + API key scope checks + hook audit log                              │          │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────┤
  │ I/O streams        │ Typed NDJSON event stream (output/events.py) → WebSocket → CLI — structured      │ Good     │
  │                    │ stdout equivalent                                                                │          │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────┤
  │ Filesystem sandbox │ Project-dir scoped tools in _make_project_tools(), absolute paths outside        │ Good     │
  │                    │ project rejected                                                                 │          │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┼──────────┤
  │ IPC                │ AgentMailbox (mailbox.py) — thread-safe per-agent message queue, send/receive    │ Good     │
  │                    │ ReAct tools; spawn_task() via task queue injection; hook events on state change  │          │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤          │
  │ Process isolation  │ WorktreeManager — per-task git worktrees under .orchid/worktrees/;               │ Partial  │
  │                    │ SubprocessRunner — child-process isolation with NDJSON stdio protocol;           │          │
  │                    │ ContainerRunner — Docker-based isolation (opt-in, graceful fallback)             │          │
  └────────────────────┴──────────────────────────────────────────────────────────────────────────────────┴──────────┘

  ---
  Remaining gaps (what a real OS has that Orchid still lacks):

  ┌───────────────────────┬─────────────────────────────────────────────────────────────────────────────────────┐
  │      OS Concept       │                                         Gap                                         │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ Preemption            │ Tasks run to completion — no pause, suspend, or time-slice                          │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ True sandbox          │ Subprocess/container isolation is opt-in; default is same Python process, same user │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ Signal handling       │ No SIGTERM/graceful-shutdown equivalent for running agents                          │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ Restart persistence   │ Mid-task ReAct checkpoints save every 5 iterations, but full in-flight task        │
  │                       │ recovery on service restart is not yet wired — tasks re-run from scratch           │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ Backpressure          │ NDJSON emitter has no buffering — slow consumers can stall the event loop           │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ CPU/latency budgets   │ Cost tracking is API-token-only; no wall-clock or compute resource limits           │
  └───────────────────────┴─────────────────────────────────────────────────────────────────────────────────────┘

  ---
  Gaps closed since last revision:

  ┌───────────────────────┬─────────────────────────────────────────────────────────────────────────────────────┐
  │      OS Concept       │                                      Closed by                                      │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ Per-user quotas /     │ V2.3 auth: JWT roles, per-user project scoping, API key scopes, audit log;          │
  │ access control        │ PostgresUserStore for shared multi-node deployments                                 │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────────────────────────┤
  │ Inter-agent messaging │ AgentMailbox (mailbox.py): thread-safe per-agent queue, send_message /              │
  │                       │ receive_message ReAct tools for direct agent-to-agent coordination                  │
  └───────────────────────┴─────────────────────────────────────────────────────────────────────────────────────┘

  ---
  Bottom line: Orchid now maps cleanly onto OS scheduling, memory hierarchy, resource accounting, lifecycle
  management, and access control — all with genuine, non-trivial implementations. The V2.3 auth layer closes
  the multi-tenancy gap with JWT sessions, role-based access, per-user project scoping, and a pluggable storage
  backend (file → PostgreSQL). The hard remaining gaps are in the isolation and preemption layers: subprocess/
  container isolation is opt-in, there is no SIGTERM equivalent for running agents, and full mid-task restart
  recovery is not yet wired end-to-end. It is closer to a production-grade agentic job server with OS-shaped
  abstractions than a true OS kernel. The next logical step toward a real Agentic OS would be always-on
  subprocess isolation with SIGTERM-safe agent suspension and automatic restart recovery from ReAct checkpoints.
