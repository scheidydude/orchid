# Orchid QA Test Plan — V2.4

_Audience: QA engineer with access to a Linux machine and an Anthropic API key._
_Orchid version: V2.4 (commit b7ededd). System under test: `orchid serve` on port 7842._

---

## Prerequisites

```bash
# Install
git clone git@github.com:scheidydude/orchid.git ~/LocalAI/orchid
cd ~/LocalAI/orchid
uv tool install .

# Config
bash scripts/setup-config.sh
# Edit ~/.config/orchid/.env:
#   ANTHROPIC_API_KEY=sk-ant-...
#   JWT_SECRET=<random-64-char-string>

# Start service
orchid serve --watch-dir ~/LocalAI --port 7842
# Or via systemd: sudo systemctl start orchid-serve

# Health check
curl http://localhost:7842/health
# Expected: {"status": "ok", "projects": N}
```

---

## Area 1 — Auth & Session

| # | Test | Steps | Expected |
|---|------|-------|----------|
| A1 | Register + login | `POST /api/auth/register` with email/password, then `POST /api/auth/login` | 200 on both; login returns `orchid_access` HttpOnly cookie |
| A2 | Access protected endpoint | Call `GET /api/auth/me` without cookie | 401 |
| A3 | Access protected endpoint (authed) | Call `GET /api/auth/me` with valid cookie | 200 + `{"email": "...", "role": "user"}` |
| A4 | Token refresh | Call `POST /api/auth/refresh` after access token expiry (or wait 15 min) | New access cookie set; old refresh token invalidated |
| A5 | Logout | `POST /api/auth/logout`, then call `/api/auth/me` | 401 after logout |
| A6 | API key create + use | `POST /api/auth/apikeys` with scopes; copy secret shown once; call any endpoint with `Authorization: Bearer ok_xxx.secret` | 200; second call to view key shows no secret |
| A7 | API key revoke | `DELETE /api/auth/apikeys/{id}`; retry with old key | 401 after revoke |
| A8 | Admin role | Login as admin; `GET /api/auth/users` | 200 + list of users |
| A9 | Non-admin blocked from admin endpoints | Login as regular user; `GET /api/auth/users` | 403 |
| A10 | Audit log written | Perform login + logout; `GET /api/audit` as admin | Entries for login + logout with timestamp and user_id |
| A11 | Web UI login gate | Open http://localhost:7842 in browser unauthenticated | Redirected to login form; no flash of main UI |
| A12 | Web UI logout | Click logout button in header | Redirected to login form; cookie cleared |

---

## Area 2 — Project Discovery & Status

| # | Test | Steps | Expected |
|---|------|-------|----------|
| P1 | Auto-discovery | Create new dir with `orchid init ~/test-proj`; wait 5s | Project appears in sidebar without restart |
| P2 | Task status | Open project with tasks; check Task Board | Tasks show correct status (`[ ]` TODO, `[x]` DONE, `[!]` BLOCKED) |
| P3 | Project health | `GET /api/projects` | All registered projects listed with `path`, `task_count`, `phase` |
| P4 | YAML config view | Open Project Config tab in web UI | `.orchid.yaml` contents displayed; edits saved on submit |

---

## Area 3 — Task Execution (Core)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| T1 | Run single task | `orchid --project PATH --run-task T001` | Task completes; result stored in `.orchid/task_results.json` |
| T2 | Auto run | `orchid --project PATH --mode auto` | All TODO tasks execute in dependency order; DONE on success |
| T3 | Parallel group | Two tasks with no `needs:` run simultaneously | Both complete; neither blocks the other |
| T4 | Task dependency | T002 has `needs:T001`; run auto mode | T001 completes before T002 starts |
| T5 | Skip task | Mark task `[~]` in tasks.md; run auto mode | Skipped task not executed |
| T6 | Rollup task | `type:rollup rollup:T001,T002 output:SPRINT.md` | SPRINT.md created with merged outputs |
| T7 | Task stream in web UI | Start a run in browser; watch Agent Stream tab | Lines appear in real time via WebSocket |
| T8 | Stop run | Click Stop button during run | Run stops; in-progress task marked BLOCKED |

---

## Area 4 — Subprocess Isolation (Phase 3)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| S1 | Pool startup | Start service; check logs | `WorkerPool started (size=4)` logged; no errors |
| S2 | Task runs in subprocess | Run any task; check logs | `worker_subprocess` process spawned; result returned |
| S3 | Worker replacement | Kill a pool worker process manually (`kill <pid>`); run task | Worker auto-replaced; task completes |
| S4 | Resource limits | Run a task that allocates >4 GB RAM (if testable) | Task killed by RLIMIT_AS; marked BLOCKED |
| S5 | CPU limit | Run a task exceeding 600 CPU seconds | Task killed by RLIMIT_CPU; marked BLOCKED |

---

## Area 5 — Suspend / Resume (Phase 4)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| SR1 | Suspend via API (subprocess mode) | Start a long task; `POST /api/projects/{id}/tasks/{task_id}/suspend` | Returns `{"state": "suspended"}`; task freezes (no progress in stream) |
| SR2 | Resume via API | Call `POST .../resume` on suspended task | Returns `{"state": "running"}`; task continues from frozen point |
| SR3 | Suspend via Web UI | Run a task; click ⏸ button on Task Board | Button appears only for running task; task freezes |
| SR4 | Resume via Web UI | Click ▶ Resume | Task continues; button reverts to ⏸ |
| SR5 | Suspend non-running task | Call suspend on a TODO task | 404 response |
| SR6 | Suspend status in run/status | Check `/api/projects/{id}/run/status` while task suspended | `"suspended": true` in response |

---

## Area 6 — Provider Fallback Chain (V2.4 new)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| F1 | Fallback config accepted | Add to `.orchid.yaml`: `providers: {task_types: {code_generate: {name: claude, fallback: [local]}}}` | No config parse error; `orchid --check-providers` shows both |
| F2 | Fallback on 503 | Mock Claude to return 503 (or temporarily use wrong API key); run a `code_generate` task with `fallback: [local]` | Task completes using `local`; log shows "Provider fallback" warning |
| F3 | No fallback configured | Remove fallback config; simulate Claude 503 | Task marked BLOCKED immediately |
| F4 | Unknown fallback provider silently dropped | Set `fallback: [nonexistent_provider]` | `nonexistent_provider` ignored; chain is just `[claude]` |
| F5 | Max fallback attempts respected | Configure 5 fallbacks; `max_fallback_attempts: 2` | Only 2 providers tried before BLOCKED |
| F6 | Rate-pressure recorded after fallback | After a fallback occurs, run another task with same primary provider | Primary provider skipped if still rate-pressured (check `_rate_flags`) |

---

## Area 7 — Graceful Shutdown & Orphan Recovery (Phase 1 & 2)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| GS1 | Clean shutdown | Start a run; send SIGTERM to orchid process | In-progress task saves checkpoint; process exits within 35 s |
| GS2 | Orphan recovery | Start a run; kill process with SIGKILL; restart | On restart, orphaned IN_PROGRESS task with recent checkpoint resumes; old tasks reset to TODO |
| GS3 | Marker file cleanup | Normal stop; check `.orchid/running` file | File absent after clean stop; present only after crash |

---

## Area 8 — Auth Edge Cases

| # | Test | Steps | Expected |
|---|------|-------|----------|
| AE1 | Expired access token | Wait 15+ minutes after login; call any endpoint with old cookie | 401 Unauthorized |
| AE2 | Refresh token rotation | Call refresh endpoint twice in quick succession with first token | Second call returns 401 (token rotated after first use) |
| AE3 | Per-user project scoping | Set `user.projects = ["/path/to/proj"]` via admin API; login as that user; access another project | 403 Forbidden |
| AE4 | Admin bypass scoping | Admin user accesses any project | 200 (admin bypasses restrictions) |
| AE5 | Deactivated user | Admin sets `is_active: false`; try to login | 401 |
| AE6 | Wrong password | `POST /api/auth/login` with bad password | 401; no token issued |
| AE7 | JWT_SECRET missing | Start service without `JWT_SECRET` in env | `RuntimeError` at startup; service does not start |

---

## Area 9 — WebSocket & Backpressure (Phase 5)

| # | Test | Steps | Expected |
|---|------|-------|----------|
| WS1 | Agent stream connects | Open Agent Stream tab in web UI during a run | Events appear in real time |
| WS2 | Slow client eviction | Simulate slow client (add 10 s delay in browser DevTools network); run a task | Client evicted after 5 s WS send timeout; no stall to other clients |
| WS3 | Heartbeat | Keep WS connection open for 30+ s with no activity | Ping frame received; connection remains alive |
| WS4 | Reconnect after eviction | Allow eviction; reload page | New WS connection established; stream resumes from current state |

---

## Area 10 — Regression

| # | Test | Verify |
|---|------|--------|
| REG1 | Unit tests pass | `pytest -m "not network" --ignore=tests/test_integration.py --ignore=tests/test_metrics.py` → 0 failures |
| REG2 | Auth tests pass | `pytest tests/test_auth.py` → 134 passed |
| REG3 | Phase 1–6 tests pass | `pytest tests/test_shutdown.py tests/test_graceful_shutdown.py tests/test_orphan_recovery.py tests/test_worker_pool.py tests/test_suspend_resume.py tests/test_cpu_accounting.py` → all pass |
| REG4 | Providers import clean | `python3 -c "from orchid.providers.registry import get_registry; get_registry()"` → no import error |
| REG5 | Fallback chain imports clean | `python3 -c "from orchid.providers.base import RetriableProviderError"` → OK |
| REG6 | Web UI loads | Open http://localhost:7842 | Login page or main UI loads with no console errors |

---

## Known Exclusions

- `tests/test_integration.py` and `tests/test_metrics.py` — hit live Claude API, result non-deterministic. Do not run in CI without mocking.
- `tests/test_providers.py` — 6 pre-existing failures unrelated to V2.4 changes.
- Network namespace isolation (Phase 6 of next-features-plan) — not yet implemented.
