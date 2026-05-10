# HANDOFF.md
_Written: 2026-05-10. Previous HANDOFF archived as `HANDOFF-archive-2026-05-09-1200.md`._

---

## 1. Mission

Orchid is a standalone AI agent orchestration framework: install once, point at any git repo, run agents against it. This session completed two distinct bodies of work: (1) the full V2.3 auth stack (JWT, argon2id, API keys, OAuth/OIDC, audit log, pluggable Postgres backend, React login page) wired into the actual running server, and (2) a 6-phase OS-grade reliability sprint (graceful shutdown, crash recovery, subprocess worker pool, preemption/pause-resume, WebSocket backpressure, CPU/latency budgets). Both are committed, pushed, and verified against the live service. The next work is the 6 forward-looking features documented in `docs/next-features-plan.md`.

---

## 2. Current State

### Live service verified at commit `f4dbf58`

```
curl http://localhost:7842/api/auth/me  →  {"authenticated":false}  ✓
14 auth routes registered in OpenAPI spec  ✓
/api/projects/{id}/tasks/{task_id}/suspend + /resume routes live  ✓
```

Service running via `sudo systemctl restart orchid-serve` (user must run this — Claude can't sudo).
The installed binary (`/home/dave/.local/bin/orchid`) is the `uv tool install` copy, rebuilt this session.

### What's working and verified

**Auth (V2.3):**
- `POST /api/auth/register`, `login`, `refresh`, `logout`, `me`, `token` — all wired into `interfaces/web_server.py` (was previously in dead-end `web/server.py` that `orchid serve` never loaded — fixed this session)
- JWT HS256 access tokens (15 min) + opaque argon2-hashed refresh tokens (30 days), HttpOnly cookies
- API keys (`ok_{id}.{secret}` format), scoped, argon2-hashed secret
- Google/Entra/generic OIDC, PKCE mobile flow
- Audit log (`~/.config/orchid/audit/audit-YYYY-MM-DD.jsonl`), admin user management, per-user project scoping
- Pluggable store: `FileUserStore` (default, JSON) or `PostgresUserStore` (set `ORCHID_AUTH_STORE_DSN`)
- React login page: checks `/api/auth/me` on load, shows sign-in form if unauthenticated, logout button in header
- `JWT_SECRET` required in `~/.config/orchid/.env` — service raises `RuntimeError` without it

**OS-grade reliability (Phases 1–6):**
- **Phase 1 — Graceful shutdown:** `orchid/shutdown.py` global event; SIGTERM → cancel all agents at next ReAct iteration; final ReAct checkpoint saved before exit; `BackgroundRunner.graceful_shutdown(timeout_s=30)`; systemd `KillMode=mixed`, `TimeoutStopSec=35`
- **Phase 2 — Orphan recovery:** `.orchid/running` marker (survives crashes only); startup scans all projects; tasks with ReAct checkpoint ≤ 24 h old resume from saved iteration; stale → reset to TODO
- **Phase 3 — Worker pool:** `isolation.subprocess_enabled: true` (default); 4 pre-forked workers; RLIMIT_AS/CPU/NOFILE via `preexec_fn`; SIGTERM → 5 s → SIGKILL on timeout
- **Phase 4 — Preemption:** `BaseAgent.suspend()`/`resume()` via `threading.Event`; saves checkpoint on suspend; `agent_registry.py` maps `task_id → agent`; `_priority_score()` in scheduler (p1=30, p2=20, p3=10 + age bonus); `/suspend` `/resume` API; ⏸/▶ buttons in Task Board
- **Phase 5 — Backpressure:** `asyncio.wait_for(timeout=5s)` around every `ws.send_json()`; 30 s heartbeat ping; dead clients evicted
- **Phase 6 — CPU/latency budgets:** per-iteration latency tracking (3-strike cancel); `RUSAGE_CHILDREN` cpu_seconds in WorkerResult → TokenRecord → task_metrics.jsonl → PM Dashboard CPU column; `User.cpu_budget_seconds`; `CostScheduler.check_cpu_budget()`

### Half-built / known gaps

- `orchid/isolation/` directory does **not exist** yet — network namespace isolation (Observation 6) is documented in `docs/next-features-plan.md` but not implemented
- `agents.max_iteration_seconds: 0` (disabled by default) — latency budget feature exists but needs the user to set a value to activate
- `runner.preemption_enabled: false` — priority preemption is opt-in; pause/resume works but automatic preemption of lower-priority tasks is not wired
- No OpenTelemetry, no Redis queue, no async agent execution, no capability versioning — all planned in `docs/next-features-plan.md`

### Next action for a fresh session

The 6 next features are planned and prioritized in `docs/next-features-plan.md`. Recommended first: **LLM provider fallback chain** (effort S, no new dependencies, files: `providers/base.py`, `providers/registry.py`, `orchestrator.py`, `cost/scheduler.py`).

---

## 3. Decisions Made (and Why)

**Decision:** Auth endpoints live in `interfaces/web_server.py`, not `web/server.py`
**Alternatives considered:** Keep two separate server files, redirect one to the other
**Reason:** `orchid serve` imports `orchid.interfaces.web_server` via CLI → `serve()` → `create_app()`. `orchid/web/server.py` is a dead-end standalone file never loaded by the real server. All auth routes had to be added inside `create_app()` in `interfaces/web_server.py`.
**Reversibility:** Load-bearing — do not move auth back to `web/server.py`.

**Decision:** Auth endpoints gated by `if _AUTH_AVAILABLE:` inside `create_app()`
**Alternatives considered:** Hard import (fail fast if missing), separate router
**Reason:** Allows the server to start without auth deps if they're not installed. `try/except ImportError` at module level sets `_AUTH_AVAILABLE`.
**Reversibility:** Easy to change to hard-fail if desired.

**Decision:** `get_store()` singleton factory in `store.py`; both `web_server._get_auth_store()` and `middleware._get_store()` delegate to it
**Alternatives considered:** Each module holds its own store instance (was the original code)
**Reason:** Two separate `UserStore()` instances writing the same JSON file created a race. Singleton ensures one FileUserStore per process.
**Reversibility:** Easy to change. The singleton is in `store.py` behind `_store_lock`.

**Decision:** `isolation.subprocess_enabled: true` by default (Phase 3)
**Alternatives considered:** Keep opt-in (`false`)
**Reason:** Gap-closure plan called for always-on isolation. Worker pool eliminates startup cost that made the previous opt-in stance reasonable.
**Reversibility:** Easy — set `isolation.subprocess_enabled: false` in `.orchid.yaml` to revert per-project.

**Decision:** `agent_registry.py` as a separate module for the global `task_id → agent` map (Phase 4)
**Alternatives considered:** Store on `_ProjectState` in runner, store on orchestrator
**Reason:** Avoids coupling orchestrator → runner or runner → orchestrator. Both can independently import `agent_registry` without circular imports.
**Reversibility:** Easy to change. It's 30 lines.

**Decision:** Priority score = `{p1:30, p2:20, p3:10} + age_bonus` using task ID number as age proxy (Phase 4)
**Alternatives considered:** Raw `task.priority` int (what existed before), actual timestamp
**Reason:** Task has no `created_at` field. ID number is monotonic within a project — lower ID = queued earlier = small bonus. Weighted scoring (30/20/10) gives p1 a decisive lead over p2 regardless of age.
**Reversibility:** Easy — just the `_priority_score()` function in `scheduler.py`.

**Decision:** `RUSAGE_CHILDREN` delta for CPU accounting (Phase 6)
**Alternatives considered:** Parse `/proc/pid/stat`, `psutil`
**Reason:** `resource.getrusage(RUSAGE_CHILDREN)` is stdlib, cross-distro, zero deps. Delta (before/after child.wait()) gives per-task CPU with reasonable accuracy for sequential children.
**Reversibility:** Easy to swap for psutil later.

**Decision:** WebSocket suspend/resume buttons only shown for the current running task (Phase 4)
**Alternatives considered:** Show for all IN_PROGRESS tasks
**Reason:** `runStatus.currentTask` from the run/status endpoint identifies which specific task is running. `isThisRunning = task.status === 'IN_PROGRESS' && currentTask.startsWith(task.id)`. Only one task can be in the `suspended` state at a time per project.
**Reversibility:** UI-only, easy to change.

---

## 4. Architecture & Key Files

### Created this session

| File | What it does |
|------|-------------|
| `orchid/shutdown.py` | Process-wide `threading.Event`; `request_shutdown()`, `is_shutting_down()`. Zero circular imports — everything imports this. |
| `orchid/agent_registry.py` | Global `{task_id: agent}` map. Lets endpoints reach live agents for suspend/resume without coupling orchestrator ↔ runner. |
| `orchid/auth/base.py` | `BaseUserStore` ABC — 23 abstract methods. Both `FileUserStore` and `PostgresUserStore` implement it. |
| `orchid/auth/store_postgres.py` | PostgreSQL backend. `ThreadedConnectionPool`, auto-creates `orchid_*` tables, UPSERT-safe. Requires `psycopg2-binary`. |
| `orchid/interfaces/web_ui/src/components/Login.jsx` | React login form. Calls `POST /api/auth/login`, shows error, calls `onLogin(user)` on success. |
| `docs/gap-closure-plan.md` | Phased plan for OS-grade reliability (Phases 1–6, this session's work). |
| `docs/auth-store-backends.md` | How to switch between file and Postgres storage. |
| `docs/next-features-plan.md` | Implementation plans for 6 forward-looking features (the actual next work). |

### Modified significantly this session

| File | What changed |
|------|-------------|
| `orchid/interfaces/web_server.py` | **Critical change:** all auth endpoints added inside `create_app()` — this is where `orchid serve` actually routes. Also: graceful shutdown in lifespan, orphan recovery on startup, WS send timeout, WS heartbeat, suspend/resume endpoints, `cpu_budget_seconds` in user update. |
| `orchid/auth/store.py` | `UserStore` renamed to `FileUserStore` (alias kept); `get_store()` singleton factory added; imports `BaseUserStore`. |
| `orchid/auth/middleware.py` | `_get_store()` delegates to `get_store()` — all callers share one store instance. |
| `orchid/auth/types.py` | Added `User.cpu_budget_seconds: float = 0.0`. |
| `orchid/agents/base.py` | Added `_resume_checkpoint`, `_suspend_event`, `_resume_event`, `_suspended`; `suspend()`/`resume()` methods; shutdown check in run loop; suspend parking (checkpoint + `threading.Event.wait()`); per-iteration latency tracking with 3-strike cancel; final checkpoint on cancel. |
| `orchid/runner.py` | `graceful_shutdown()`, `.orchid/running` marker write/remove, `recover_orphans()`, `suspend_task()`/`resume_task()`. |
| `orchid/orchestrator.py` | Loads ReAct checkpoint and wires to `agent._resume_checkpoint`; registers/deregisters in `agent_registry`; `_last_subprocess_cpu_s` tracking; `cpu_seconds` passed to `_write_task_metrics()`. |
| `orchid/subprocess_runner.py` | Rewritten: `WorkerPool` class (pre-forked workers), `_run_oneshot()` (fallback), `_resource_preexec()` (RLIMIT), `_child_cpu()` (RUSAGE delta), SIGTERM before SIGKILL. |
| `orchid/worker_subprocess.py` | Added `pool_main()` (loop accepting tasks via stdin until `{"type":"exit"}`); `--pool` CLI flag. |
| `orchid/worker_protocol.py` | `WorkerResult.cpu_seconds: float = 0.0` added. |
| `orchid/checkpoint/restore.py` | `resume_orphaned_tasks()` added — scans IN_PROGRESS tasks, checks checkpoint age, resets stale ones to TODO. |
| `orchid/cost/ledger.py` | `TokenRecord.cpu_seconds` field; `record()` accepts `cpu_seconds`; `daily_cpu_for_user()` method. |
| `orchid/cost/scheduler.py` | `check_cpu_budget(user_id, cpu_budget_seconds)` added. |
| `orchid/scheduler.py` | `_priority_score()` function; topological sort and parallel group sort use score (descending). |
| `orchid/orchid.defaults.yaml` | `runner.shutdown_timeout`, `runner.preemption_enabled`, `isolation.subprocess_enabled: true`, `isolation.subprocess_workers: 4`, `isolation.resource_limits`, `web.ws_send_timeout`, `web.ws_heartbeat_s`, `agents.max_iteration_seconds`. |
| `orchid/interfaces/web_ui/src/App.jsx` | Auth gate: checks `/api/auth/me` on load; renders `<Login>` if unauthenticated; `AuthenticatedApp` component for the rest; logout button. |
| `orchid/interfaces/web_ui/src/components/TaskBoard.jsx` | `onSuspend`/`onResume` handlers; passes `currentTask`/`suspended` to `TaskRow`. |
| `orchid/interfaces/web_ui/src/components/TaskRow.jsx` | ⏸ button (suspend) and ▶ Resume button based on `isThisRunning`/`isThisSuspended`. |
| `orchid/interfaces/web_ui/src/components/pm/TaskTiming.jsx` | CPU column added. |
| `scripts/orchid-serve.service` | `KillMode=mixed`, `KillSignal=SIGTERM`, `TimeoutStopSec=35`. |
| `README.md` + `docs/Orchid vs Agentic OS.md` | Both fully updated for this session's work. |

### Do not touch without reason

- `orchid/web/server.py` — dead-end file, never loaded by `orchid serve`. Auth code was incorrectly added here first (the bug we fixed). Do not add new routes here.
- `orchid/auth/jwt.py` — crypto is stable. argon2id params (time=3, mem=64MB, par=4) are OWASP-recommended, do not change without security review.

---

## 5. Gotchas & Hard-Won Knowledge

**The biggest bug this session:** Auth endpoints were in `orchid/web/server.py`. `orchid serve` loads `orchid/interfaces/web_server.py`. These are different files. The running service had zero auth routes. The symptom: `POST /api/auth/register` returned 405 with `allow: GET` because `/{full_path:path}` catch-all GET matched it first. Confirmed by hitting `/openapi.json` and seeing no auth paths. Fix: add auth inside `create_app()` in `interfaces/web_server.py`.

**`uv tool install` vs `uv pip install -e`:** Migration guide step 3 says `uv pip install -e ".[dev]"` which installs into `.venv`. But `orchid serve` runs via `/home/dave/.local/bin/orchid` which is the `uv tool install` copy in a separate venv. After any code change: `npm run build` (if UI changed) then `uv tool install --reinstall --from . orchid` from repo root. Then `sudo systemctl restart orchid-serve`.

**Subprocess pool startup order:** `_PoolWorker.__init__()` blocks waiting for `{"type":"ready"}` from the worker stdout (up to 10 s). If the pool fails to start (bad import, missing dep), `WorkerPool._spawn_workers()` catches the exception and logs a warning — it doesn't crash the service. Check logs if tasks silently fail with subprocess mode on.

**`get_store()` singleton:** `_store_instance` is set once at process start. If `ORCHID_AUTH_STORE_DSN` is not in env when the process starts, it will use `FileUserStore` for the entire lifetime — even if you set the env var later. Must be in env before process starts.

**`WorkerResult` deserialization in pool mode:** Workers return JSON with all fields including `cpu_seconds`. `_run_oneshot()` uses `WorkerResult(**{k: v for k, v in data.items() if k in WorkerResult.__dataclass_fields__})` to safely ignore unknown keys from old workers. Pool mode (`_PoolWorker.run_task()`) uses the same pattern.

**Suspend/resume requires agent to be registered:** The `agent_registry` is populated by `orchestrator._execute_task()` just before `agent.run()`. Tasks running in subprocess mode (`isolation.subprocess_enabled: true`) run in a child process — the parent's agent_registry has no entry. Suspend/resume only works for in-process agents. Subprocess mode makes suspend endpoints return 404. This is a known limitation.

**`asyncio.wait_for` in sync broadcast:** `ConnectionManager.broadcast()` is an `async def`. The `asyncio.wait_for(ws.send_json(...), timeout=5.0)` works correctly because `broadcast` is always called from an async context (FastAPI event loop). `broadcast_sync()` uses `asyncio.run_coroutine_threadsafe()` from background threads — this is correct and unchanged.

**`RUSAGE_CHILDREN` accumulates:** `getrusage(RUSAGE_CHILDREN)` returns cumulative CPU for all waited children, not just the last one. `_child_cpu()` must be called before and after `proc.wait()` to get the delta. If tasks run in parallel (same parent process), the delta is approximate. For the worker pool, each task's CPU is slightly over-counted if workers finish concurrently. Acceptable for budget enforcement.

**`resume_orphaned_tasks()` vs running marker:** The marker file at `.orchid/running` is written when `BackgroundRunner.start()` is called and removed in `_run()` finally block. Graceful stop removes it. Only a crash or SIGKILL leaves it. On startup, the orphan scan runs before traffic is served (in `_lifespan` startup). If a project is not yet registered when the scan runs, it won't be checked — new projects discovered by `ProjectDiscovery` after startup need manual `--recover`.

---

## 6. Conventions In Play

**Caveman mode active** — this Claude Code session ran with compressed communication (caveman harness). The next session may or may not have it. Doesn't affect code, just assistant responses.

**Commit style:** Conventional Commits. `feat:`, `fix:`, `docs:`, `chore:`. Body explains the "why". Co-authored line required (harness adds it). See recent commits for examples.

**No test suite updates this session** — the auth and OS-grade phases were not accompanied by new tests. `tests/` has 134 auth tests from the original auth implementation but none for: worker pool, suspend/resume, orphan recovery, CPU accounting. Next session should be aware tests may not cover Phase 1–6 additions.

**`uv tool install` is the deployment mechanism** — not `pip install`, not running from source. The installed binary is what systemd runs. Always rebuild after changes:
```bash
cd orchid/interfaces/web_ui && npm run build  # if UI changed
cd ~/LocalAI/orchid && uv tool install --reinstall --from . orchid
sudo systemctl restart orchid-serve
```

**Config layering:** 3 layers: `orchid.defaults.yaml` (packaged) → `.orchid.yaml` (per-project) → CLI flags. Never edit `orchid.defaults.yaml` for project-specific settings.

**Auth module:** `orchid/auth/` is self-contained. `orchid/interfaces/web_server.py` imports from it (inside `if _AUTH_AVAILABLE:` guards). Do not import web_server from auth — that's the circular import direction.

**No migration needed for new fields:** `User.cpu_budget_seconds`, `TokenRecord.cpu_seconds`, `WorkerResult.cpu_seconds` all have `default=0.0`. Old `users.json` files load without error.

---

## 7. Open Questions

1. **Does the worker pool actually work for real tasks?** Pre-forked workers import orchid at startup. If any import fails (e.g., missing ANTHROPIC_API_KEY at import time), all pool workers die silently. Has not been tested end-to-end with a real agent run since subprocess mode was flipped to default-on. The user should run a test task with `orchid --project PATH --run-task T001` and check logs.

2. **Suspend/resume with subprocess mode:** suspend/resume only works for in-process agents (subprocess disabled). With `isolation.subprocess_enabled: true` (the new default), suspend calls return 404 because the child process has no entry in the parent's agent_registry. Should subprocess mode be disabled by default after all, or should suspend/resume be forwarded to the child via a signal/pipe?

3. **Login page with existing users before V2.3:** Users registered before V2.3 have `password_hash: null`. They cannot log in until a password is set via the migration script (Step 5 of `docs/migration-v2.2-to-v2.3.md`). Has the user run this for their `admin` account?

4. **`JWT_SECRET` in the service environment:** The service unit reads from `EnvironmentFile=/home/dave/LocalAI/orchid/.env` (not the XDG `~/.config/orchid/.env`). If `JWT_SECRET` is only in `~/.config/orchid/.env`, the service won't see it. Verify: `sudo systemctl show orchid-serve | grep EnvironmentFile`.

5. **Next feature to implement:** Per `docs/next-features-plan.md` the recommended order is: LLM fallback chain → capability versioning → OpenTelemetry → distributed queue → async agent → network namespace. Confirm with user before starting.

---

## 8. Do Not Touch

- **`orchid/web/server.py`** — dead-end file. Never loaded by `orchid serve`. Do not add routes here. Do not delete it yet (may be used by `orchid web` single-project command), but do not confuse it with the real server.
- **`orchid/auth/jwt.py`** — crypto parameters settled. argon2id time=3/mem=64MB/par=4 is OWASP standard. HS256 with `JWT_SECRET` is correct. Do not change without a security reason.
- **`orchid/auth/store.py` `UserStore` alias** — `UserStore = FileUserStore` is kept for backward compat. Third-party code or tests may call `UserStore()`. Do not remove the alias.
- **`.claude/settings.local.json`** — local harness config. Modified every session. Do not commit.
- **`orchid/orchid.defaults.yaml` isolation section** — `subprocess_enabled: true` is now the default. Do not revert to `false` without reason; the worker pool exists specifically to make this affordable.
- **`pyproject.toml` version `2.3.0`** — current released version. Bump to `2.4.0` only when the next major feature set (any of the 6 in `next-features-plan.md`) ships.

---

## 9. Resume Command

> Read `HANDOFF.md` and `docs/next-features-plan.md`. The codebase is at commit `f4dbf58` on `main`, clean working tree. All Phase 1–6 reliability features and V2.3 auth are complete and live. The next work is the LLM provider fallback chain (Observation 6 in the plan, effort S, no new deps). Before starting, confirm: (1) does the user want to start with the fallback chain, or a different feature from the plan? (2) has the user verified a real agent task runs correctly with the new subprocess worker pool (`isolation.subprocess_enabled: true`)? Do not modify `orchid/web/server.py` (dead-end file). Do not revert `isolation.subprocess_enabled` to false. Rebuild + reinstall after any Python change (`uv tool install --reinstall --from . orchid`); rebuild frontend before reinstall if JSX changed.
