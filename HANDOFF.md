# HANDOFF.md
_Written: 2026-05-07. The 7-phase improvement sprint is **complete**. Everything is committed, pushed, and passing. No uncommitted changes._

---

## 1. Mission

Orchid is a standalone AI agent orchestration framework that manages multi-agent pipelines over external projects. This session completed a structured 7-phase hardening sprint: security → native git → worktree isolation → parallelism → dynamic task spawning → cross-project agent pool → cost scheduling. All phases are done, committed, and pushed to `main`. The next session starts fresh with no outstanding sprint work.

---

## 2. Current State

### Sprint complete — all 7 phases committed and verified

- **Commit:** `4cd85be` — `feat(phase7): cost tracking and budget-aware scheduling`
- **Tests:** 1152 pass, 1 skip (MCP integration, POSIX-only), 0 failures
- **Working directory:** clean — only `.claude/settings.local.json` modified (local harness config, do not commit)
- **tasks.md:** all tasks T151–T208 marked `[x]` in DONE section

### What's built and verified (by phase)

**Phase 1 — Security hardening (T151–T162)**
- `orchid/hooks/circuit_breaker.py` — `CircuitBreakerRegistry` singleton, per-event-type breakers, 55 tests
- `orchid/hooks/audit.py` — `AuditLogger` writing `.orchid/audit_log.jsonl`, 60 tests
- Both wired into `orchid/hooks/loader.py` at load time
- Per-agent `allowed_tools` frozensets on TesterAgent, ReviewerAgent, ResearcherAgent; DeveloperAgent intentionally unrestricted

**Phase 2 — Native git tools (T163–T169)**
- `orchid/tools/git.py` — 12 git functions (status, diff, log, commit, push, pull, branch ops)
- Registered in `_make_project_tools()` behind `agents.git_tools_enabled: true` config guard
- DeveloperAgent allowed_tools includes all git functions; 50 tests in `tests/test_git_tools.py`

**Phase 3 — Worktree isolation (T170–T175)**
- `orchid/worktree.py` — `WorktreeManager` LRU cache of git worktrees, 46 tests
- `WorktreeError` exception exported (required for imports from delegator)
- Wired into `AgentDelegator.delegate()` behind `worktree.enabled: false` (opt-in)
- `task_id` sanitized against path escape (`/` and `..` → `_`)

**Phase 4 — Parallel task dispatch (T176–T185)**
- `orchid/scheduler.py` — `DependencyGraph`, `ParallelGroupDetector`, `Scheduler` with parallel group computation
- `orchid/runner.py` — `BackgroundRunner._run_loop()` rewritten for parallel dispatch via ThreadPoolExecutor groups
- `orchid/session.py` — `Lock()` → `RLock()` (re-entrancy from `_execute_task` + exception handlers)
- Critical bug fixed: `completed_ids` was never updated in `_run_loop`, so tasks with completed parents never ran. Fixed by refreshing `completed_ids` from task statuses after each group.
- Provider semaphores limit concurrent API calls per provider

**Phase 5 — Dynamic task spawning (T186–T192)**
- `Session.inject_task()` — thread-safe runtime task injection with RLock
- `orchid/tools/task_injection.py` — `spawn_task()` agent tool using `threading.local()` for per-thread session ref (thread-safe for parallel dispatch); `set_active_session()` called by orchestrator before each task
- `spawn_task` registered in `_make_project_tools()` (available to all agents; not in TesterAgent/ReviewerAgent frozensets)
- DeveloperAgent system prompt documents spawn_task with usage rules

**Phase 6 — Cross-project agent pool (T193–T199)**
- `orchid/agent_pool.py` — `AgentPool` LRU cache of pre-instantiated agents keyed by (agent_type, model_key), idle eviction thread, thread-safe RLock; `AgentPoolError` exported
- `get_agent_pool()` singleton; `reset_agent_pool()` for testing
- Wired into `Orchestrator._get_agent()` with fallback to direct creation; also in `AgentDelegator._acquire_agent()`
- `agent_pool.enabled: false` in defaults (opt-in); 32 tests

**Phase 7 — Cost tracking and budget scheduling (T200–T208)**
- `orchid/cost/ledger.py` — `CostLedger` in-memory + JSONL-backed token/cost recorder; `daily_spend()`, `daily_tokens()`, `budget_remaining()` use UTC timestamps
- `orchid/cost/scheduler.py` — `CostScheduler` with budget cap enforcement (`BudgetBlockedError`), 429 rate-limit backoff (`ThrottleBlockedError`), `select_cheapest_provider()`; spec-compat shims: `CostAwareScheduler` alias, `set_rate_pressure()`, `_rate_flags` dict
- Wired into `Orchestrator._execute_task()` (pre-task budget/rate checks, post-task cost recording, 429 detection)
- **Critical:** cost-aware routing gated behind `cost.enforce_budget` or `cost.prefer_local_under_pressure` (both `false` by default). Without this gate, `select_cheapest_provider()` overrides type-based routing and sends `type:draft` tasks to Anthropic.
- `getattr` guards on `_cost_ledger`/`_cost_scheduler` in orchestrator methods because some tests use `Orchestrator.__new__()` to bypass `__init__`
- 123 tests in `tests/test_cost_ledger.py` + `tests/test_cost_scheduler.py`

### Next action

The sprint is complete. No immediate follow-up is required. The user's most recent message was asking to "append phase 7 tasks" — but those tasks are already in tasks.md and marked done. They may have been confused, or they want to start a new sprint. **Ask what's next before doing anything.**

---

## 3. Decisions Made (and Why)

**Decision:** Orchid's local model built richer implementations than the phase specs. We validated and kept them rather than refactoring to match specs.
- **Examples:** `CircuitBreakerRegistry` (event-level, not per-hook), `CostScheduler` (full budget cap + rate-limit, not just a selector), `AgentPool` (LRU cache, not queue-based dispatch), `WorktreeManager` (full lifecycle manager, not the minimal spec).
- **Reason:** All implementations pass their tests and are functionally correct. Refactoring to match specs would break tests and ship nothing.
- **Reversibility:** Each module is self-contained. Refactor only if a specific gap is identified.

**Decision:** Spec-required class/function names added as aliases/shims when Orchid built under different names.
- **Examples:** `CostAwareScheduler = CostScheduler subclass`, `set_rate_pressure()` module fn, `WorktreeError(Exception)` added to worktree.py, `AgentPoolError(Exception)` added to agent_pool.py.
- **Reason:** Imports in other modules and tests need these names. Adding shims is non-breaking.
- **Reversibility:** Aliases cost nothing; keep them.

**Decision:** `cost.enforce_budget` and `cost.prefer_local_under_pressure` default to `false` — cost-aware routing is opt-in.
- **Reason:** Without this gate, `select_cheapest_provider()` overrode type-based routing (sending `type:draft` to Anthropic). This broke `tests/test_stream_json_cli.py` — verified by stash test.
- **Reversibility:** Easy to change in `orchid.defaults.yaml`. But the gate must stay or routing regresses.

**Decision:** `session._lock = threading.RLock()` (was `Lock()`).
- **Reason:** T184 review found that `_execute_task` calls `session.update_task_status()` and the exception handler in `_execute_task_with_semaphore` also calls it on the same thread. `Lock` would deadlock on re-entry; `RLock` doesn't.
- **Reversibility:** Load-bearing. Don't revert.

**Decision:** `threading.local()` for `spawn_task`'s session reference, not a module-level global.
- **Reason:** Phase 4 dispatches tasks in parallel threads. A module-level global would let task B's `set_active_session()` overwrite task A's reference mid-run. `threading.local()` gives each worker thread its own session.
- **Reversibility:** Load-bearing. Don't revert.

**Decision:** `completed_ids` refreshed from task statuses after each parallel group in `_run_loop`.
- **Reason:** `completed_ids` was initialized as `set()` and never updated. `DependencyGraph.get_ready_tasks(completed_ids)` checks `all_deps.issubset(completed_ids)` — with an empty set, tasks with completed parents were never ready. Bug made the scheduler functional for independent tasks only.
- **Reversibility:** Load-bearing. Don't revert.

**Decision:** `Orchestrator._cost_ledger` and `_cost_scheduler` accessed via `getattr(..., None)` instead of direct attribute access.
- **Reason:** Several tests use `Orchestrator.__new__(Orchestrator)` to bypass `__init__`, then manually set the attributes they need. Without the guard, `_record_cost_for_task` raises `AttributeError` on those test instances.
- **Reversibility:** Defensive coding, keep it.

---

## 4. Architecture & Key Files

### Sprint deliverables (all new this sprint)

| File | Purpose |
|------|---------|
| `orchid/hooks/circuit_breaker.py` | Event-level circuit breaker registry. 280 lines. |
| `orchid/hooks/audit.py` | Audit logger → `.orchid/audit_log.jsonl`. 191 lines. |
| `orchid/tools/git.py` | 12 git tool functions for agents. 288 lines. |
| `orchid/worktree.py` | `WorktreeManager` LRU cache of git worktrees. 603 lines. |
| `orchid/scheduler.py` | Dependency graph, topological sort, parallel group detection. 399 lines. |
| `orchid/tools/task_injection.py` | `spawn_task()` agent tool + `set_active_session()` using `threading.local()`. 219 lines. |
| `orchid/agent_pool.py` | LRU cache of pre-instantiated agent objects. `AgentPoolError`. 317 lines. |
| `orchid/cost/ledger.py` | Token/cost ledger, JSONL persistence. `daily_spend()` uses UTC. 363 lines. |
| `orchid/cost/scheduler.py` | Budget enforcement, rate-limit backoff, provider selection. `CostAwareScheduler` alias. 506 lines. |
| `phase_1_tasks.md` – `phase_7_tasks.md` | Sprint task specs. Read-only reference. |

### Modified significantly this sprint

| File | What changed |
|------|-------------|
| `orchid/hooks/loader.py` | Added circuit breaker + audit wiring; timing/status tracking in HTTP/shell handlers. |
| `orchid/agents/base.py` | `allowed_tools` frozenset filter; `spawn_task` registered in `_make_project_tools()`; git tools registered behind config guard. |
| `orchid/agents/developer.py` | System prompt extended with git tools section + dynamic task spawning section. |
| `orchid/agents/delegator.py` | Worktree isolation opt-in path; pool-based agent acquisition via `_acquire_agent()`; delegation recording. |
| `orchid/orchestrator.py` | `_resolve_provider()` extracted; `_get_agent()` uses pool; cost wiring (pre-task checks, post-task recording, 429 detection); `set_active_session()` before dispatch; gated cost-aware routing. 1204 lines. |
| `orchid/runner.py` | `_run_loop()` rewritten for parallel groups; provider semaphores; `completed_ids` refresh fix. |
| `orchid/session.py` | `Lock()` → `RLock()`; `inject_task()` method; `set_active_session()` instance method. |
| `orchid/orchid.defaults.yaml` | `agent_pool`, `worktree`, `cost`, `agents.allowed_tools`, `git_tools_enabled`, `runner.provider_concurrency` config blocks. |

### Do not touch (and why)

- `orchid/agents/developer.py:allowed_tools` — DeveloperAgent is intentionally unrestricted (no frozenset). Don't add one.
- `orchid/hooks/circuit_breaker.py` / `orchid/hooks/audit.py` — Orchid's richer implementation. Tests pass against it. Don't refactor to match the phase_1 spec.
- `phase_N_tasks.md` files — read-only references. All tasks are done; editing them changes nothing useful.
- `orchid/cost/scheduler.py:reset_cost_scheduler()` — sets `_scheduler_instance = None` after reset (this was a bug fix; a pre-existing test `test_reset_cost_scheduler_clears_singleton` verifies it). Don't "simplify" back to just calling `.reset()`.

---

## 5. Gotchas & Hard-Won Knowledge

**Local model marks tasks DONE without writing files.** The model reads a file, echoes its contents as Final Answer, Orchid marks DONE. Every task that edits `base.py` or `orchestrator.py` has a mandatory `bash grep` verification step at the end. If grep doesn't find the new symbol, the write failed. This is already in the phase task specs.

**`Orchestrator.__new__()` pattern in tests.** `tests/test_rollup.py` (and a few others) create orchestrator instances via `__new__` to avoid the full init. New attributes added to `__init__` are invisible to these tests until accessed via `getattr(..., None)`. The Phase 7 cost attributes hit this — fixed with `getattr` guards. Watch for this when adding new `self._foo` attributes to `Orchestrator.__init__`.

**`date.today()` vs UTC in cost ledger.** `TokenRecord.timestamp` uses `datetime.now(UTC).isoformat()`. `daily_spend()` must compare using `datetime.now(UTC).date()`, not `date.today()`. On machines where local timezone ≠ UTC, the dates differ across midnight. This tripped us up — test passed in CI (UTC) but failed locally.

**`set_rate_pressure()` must NOT touch the CostScheduler singleton.** Earlier implementation called `singleton.record_429()` when setting rate pressure. This leaked state between tests: a test that set pressure would contaminate the next test's scheduler, causing `ThrottleBlockedError` and routing tasks to Anthropic. Now `set_rate_pressure()` only updates `_rate_flags` dict — completely isolated from the singleton.

**Cost-aware routing must be gated.** `CostScheduler.select_cheapest_provider()` with no budget pressure returns `available_providers[0]`. If `anthropic` is listed as available first, ALL tasks get routed there regardless of type. The gate `cost.enforce_budget or cost.prefer_local_under_pressure` (both false by default) prevents this. Removing the gate breaks `test_stream_json_cli`.

**`source .venv/bin/activate` required for pytest.** `python` not on PATH; venv python has test deps. Always: `source .venv/bin/activate && python -m pytest`.

**`task_results.json` is NDJSON.** `json.load()` fails. Parse line by line with `json.loads(line)`.

**`completed_ids` must be refreshed between parallel groups.** The scheduler's `compute_groups()` checks `graph._deps.get(tid) - completed_ids` to determine readiness. If `completed_ids` is empty (the pre-fix state), tasks with any dependency are never scheduled after their parent completes — groups would be `[[T1]]` on first pass and `[]` on second pass, causing early exit.

**`tests/test_parallel_runner.py` and `tests/test_agent_pool.py` are slow.** The parallel runner tests sleep 0.05s per task; the agent pool tests have 18s of eviction waits. Exclude them for fast iteration: `pytest tests/ --ignore=tests/test_agent_pool.py --ignore=tests/test_parallel_runner.py`.

---

## 6. Conventions In Play

- **Task format:** `- [ ] **T001** Title \`type:code_generate\` \`p1\` \`needs:T002\` \`model:local\``
- **Model routing:** `model:local` → DeveloperAgent (local LLM); `type:code_review` → ReviewerAgent (Claude API). No annotation → defaults from `orchid.defaults.yaml`.
- **Commit style:** conventional commits (`feat:`, `fix:`, `docs:`, `chore:`), co-authored with Claude Sonnet 4.6.
- **No code comments** unless the WHY is non-obvious. No multi-line docstrings.
- **Single-file per task** for `orchid/agents/base.py` and `orchid/orchestrator.py` edits — local model gets confused on multi-file tasks for large files.
- **Grep verification step** at end of every task editing base.py or orchestrator.py — local model frequently marks tasks done without writing anything.
- **Phase files read-only** once execution starts. Edit `tasks.md`, not `phase_N_tasks.md`.
- **Tests run from venv:** `source .venv/bin/activate && python -m pytest tests/ -q`

---

## 7. Open Questions

The 7-phase sprint is complete. There is no planned follow-on work. Open questions for the next session:

1. **What's next?** The user asked to "append phase 7 tasks" but those are already done. Was that a mistake, or is there a Phase 8 / new sprint in mind?
2. **Deploy to server?** Phases 3–7 are marked "deploy after phase: yes" in their spec files. All are opt-in via config so backward-compatible. Has any of this been deployed, or is it all running only in local dev?
3. **Phase 4 production readiness:** The parallel scheduler is new and handles dependency resolution differently than the old sequential loop. Has it been exercised on a real project (not just tests)?

---

## 8. Do Not Touch

- **`orchid/agents/developer.py`** — no `allowed_tools` frozenset. Intentionally unrestricted. Don't add one.
- **`orchid/session.py:_lock`** — must be `RLock()`, not `Lock()`. Re-entrancy required.
- **`orchid/runner.py:_run_loop`** — `completed_ids` refresh after each group is load-bearing. Don't "simplify" it away.
- **`orchid/cost/scheduler.py:set_rate_pressure`** — must only update `_rate_flags`, never touch the singleton. Any change here risks test contamination and production routing bugs.
- **`orchid/orchestrator.py:_resolve_provider`** cost-routing block — the `_cost_routing_enabled` gate must stay. Without it, all tasks route to Anthropic regardless of type.
- **`orchid/tools/task_injection.py:_local`** — must be `threading.local()`, not a module-level variable. Parallel tasks share the module; `threading.local()` isolates per-thread session refs.
- **`orchid/worktree.py:task_id` sanitization** — `task_id.replace("/", "_").replace("..", "_")` in `create()` and `remove()`. Path escape prevention.
- **`HANDOFF-archive-2026-05-06-1400.md`** — the prior handoff from when Phase 1 was just complete. Historical reference only.

---

## 9. Resume Command

> Read `HANDOFF.md`. The 7-phase sprint is complete: 1152 tests pass at commit `4cd85be`. Working directory is clean (only `.claude/settings.local.json` modified — do not commit it). **Before doing anything, ask the user what they want to work on next.** Don't start a new sprint, don't modify any existing files, and don't run Orchid until you know the scope. If the user wants a Phase 8, start by reading `phase_1_tasks.md` through `phase_7_tasks.md` to understand the task format before drafting new tasks.
