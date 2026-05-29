# HANDOFF.md
_Updated: 2026-05-29. Previous HANDOFF archived as `HANDOFF-archive-2026-05-29-1043.md`._

---

## 1. Mission

Orchid is a standalone AI agent orchestration + multi-user agentic OS. The multi-user system (Phases 1–5, post-v3 hardening) was already complete. This session closed the last major gap: the CLI was a single-user local tool with zero auth integration — vault credentials, budget limits, and MCP catalog ACLs didn't apply to any `orchid` invocation. We wired that up.

---

## 2. Current State

### Working and verified at `5f69087`

**CLI auth integration — all 4 phases shipped:**

- `orchid login [--server URL] [--username]` — POSTs to `/api/auth/login`, captures refresh token from `Set-Cookie: orchid_refresh` header, stores session at `~/.config/orchid/cli_session.json` (mode 0600) with `user_id`, `username`, `role`, `access_token`, `refresh_token`, `server_url`, `issued_at`.
- `orchid logout` — POSTs to `/api/auth/logout` with `refresh_token` in JSON body (revokes server-side), deletes session file.
- `orchid whoami` — GETs `/api/auth/me` with Bearer token; falls back to cached session info if server unreachable.
- `--mode auto` and `--run-task`: calls `BudgetGuard(user_id).check()` + `check_cpu()` before run; wraps `Orchestrator` execution in `vault_env_context(user_id)`; snapshots `CostLedger` total before and reads delta after; calls `BudgetGuard.record(delta)` + `record_cpu(elapsed)` in finally.
- `orchid mcp ls` / `orchid mcp call`: uses `MCPManager.connect_for_user(user_id, role, vault_store, users_dir)` when logged in (per-user catalog ACLs + private servers + vault credential injection). Falls back to anonymous `connect()` otherwise.
- No session = silent fallback to old anonymous single-user mode. Zero breaking change.

**web_server.py logout patched:** logout endpoint now reads `refresh_token` from JSON body as fallback (was cookie-only). Enables proper server-side token revocation from CLI.

**CLAUDE.md updated:** CLI section now documents all flags and subcommands that existed but were undocumented (13 flags, 5 subcommands). D0066 added.

**Test suite:** 916 tests passing (excluding 3 known pre-existing flaky tests — see §5). No regressions.

### Half-built / deferred

**Phase 5 (CLI API wrappers)** — `orchid user list/invite`, `orchid scheduler list/run`, `orchid audit` — was planned as optional thin `httpx` wrappers around existing API endpoints. Not implemented. Requires Phase 1 session token to auth. Documented in `docs/cli-auth-plan.md`.

### Next action for fresh session

Phase 5 if desired. Otherwise the CLI auth work is complete — pick up from `docs/cli-auth-plan.md` §Phase 5 or move on to other features.

---

## 3. Decisions Made (and Why)

**Decision:** Server-based login (POST to running `orchid serve`) rather than direct `FileUserStore` validation.
- **Alternatives considered:** Bypass the server entirely, read `~/.config/orchid/users.json` directly like `sudo` reads `/etc/shadow`.
- **Reason:** Server auth goes through the same code path as web login, fires audit logs, handles `PostgresUserStore` transparently, and works for remote Orchid instances. Direct store access would only work for local `FileUserStore` and bypass audit logging.
- **Reversibility:** Medium effort to change. Would need a new code path in `cli_auth.py`.

**Decision:** Phases 2–4 (vault, budget, MCP) are purely local — no server contact required after login.
- **Alternatives considered:** Have budget recording POST to server; have MCP catalog fetched from server API.
- **Reason:** All the data lives on-disk (`~/.config/orchid/`). Local file ops are faster, work offline, and reuse existing modules (`VaultStore`, `BudgetGuard`, `MCPCatalogStore`) unchanged.
- **Reversibility:** Easy to add server-side recording later if needed.

**Decision:** `orchid mcp ls/call` uses `connect_for_user()` *instead of* `connect()` when logged in — not both.
- **Alternatives considered:** Call both `connect()` (project servers from `.orchid.yaml`) and `connect_for_user()` (catalog + private servers).
- **Reason:** `connect_for_user()` at line 320 does `self._adapters = {}` — it replaces adapters entirely, so calling both would require merging `_server_config` dicts, adding complexity. The use cases are distinct: logged-in users want catalog/private servers; anonymous CLI users want project servers. Merging adds confusion.
- **Reversibility:** Low effort — could merge configs before connecting if the user wants both sets visible simultaneously.

**Decision:** Capture refresh token from `Set-Cookie` header in httpx response rather than modifying login endpoint to return it in JSON body.
- **Alternatives considered:** Add `refresh_token` to login JSON response (like the mobile PKCE endpoint does).
- **Reason:** Login endpoint already returns the refresh token via `Set-Cookie: orchid_refresh=...`, and httpx's `response.cookies` parses Set-Cookie headers. No server change needed.
- **Reversibility:** Easy to switch — login endpoint change would be 1 line.

**Decision:** logout endpoint modified to accept `refresh_token` in JSON body (not cookie-only).
- **Alternatives considered:** Accept dangling refresh tokens that expire naturally after 30 days; use API key instead of refresh token for CLI.
- **Reason:** Proper revocation on logout is correct security behavior. The change is 5 lines mirroring the same pattern already in the refresh endpoint.
- **Reversibility:** Additive only — cookie path still works.

---

## 4. Architecture & Key Files

### Created this session

```
orchid/interfaces/cli_auth.py   CLI session store + server auth helpers
                                load_cli_session() — read session, no server
                                save_cli_session(data) — write 0600
                                clear_cli_session() — delete file
                                _try_refresh(session) — POST /api/auth/refresh
                                get_valid_session() — load + auto-refresh if >7h old
docs/cli-auth-plan.md           Phase 1–5 plan with decision rationale
```

### Modified this session

```
orchid/interfaces/cli.py        +login/logout/whoami subcommands (Phase 1)
                                +_snapshot_ledger_cost() helper
                                +_record_run_cost() helper
                                _cmd_auto() — vault + budget wrapping (Phases 2+3)
                                _cmd_run_task() — vault + budget wrapping (Phases 2+3)
                                mcp ls() — connect_for_user() when session (Phase 4)
                                mcp call() — connect_for_user() when session (Phase 4)

orchid/interfaces/web_server.py logout endpoint: reads refresh_token from JSON body
                                (fallback to cookie; matches pattern in refresh endpoint)

CLAUDE.md                       CLI section: full flag/subcommand reference added
                                D0066 added (CLI auth architecture decision)
                                "Auth gap" note replaced with "Auth (D0066)" description
                                Critical Gotchas: cli_session.json note added
```

### Should NOT be touched

```
orchid/web/server.py            DEAD FILE — never loaded by orchid serve. All routes in
                                orchid/interfaces/web_server.py.

orchid/interfaces/portal/
  vite.config.js                base: '/app/' is load-bearing. Do not change.

orchid/auth/jwt.py              Crypto params settled.

.claude/settings.local.json    Local harness config. Never commit.
```

---

## 5. Gotchas & Hard-Won Knowledge

**`connect_for_user()` resets `self._adapters = {}`** (line 320 in `manager.py`). Calling it after `connect()` will wipe project-level servers. This is why we don't call both — pick one path per invocation.

**Refresh token is in Set-Cookie, NOT the login JSON response.** `response.cookies.get("orchid_refresh")` on the httpx response object works. Cookie name is `orchid_refresh` (defined as `_COOKIE_REFRESH` in `web_server.py` line 88).

**`_snapshot_ledger_cost()` creates a temporary `CostLedger` instance** (not the singleton) to read the on-disk total before the run. The orchestrator creates the singleton during `__init__` via `configure_cost_ledger()`. After the run, `get_cost_ledger()` returns the singleton with the updated total. Delta = after − before = this run's cost.

**`vault_env_context` uses thread-local overrides, NOT `os.environ`.** Provider code must use `get_env(key)` not `os.environ.get(key)`. CLI vault injection only works if providers in your project already use `get_env()`.

**`BudgetGuard` silently skips unknown `owner_id`** — if the user doesn't exist in the store (e.g., `ORCHID_AUTH_STORE_DSN` changed but vault key didn't), budget ops are no-ops, not errors.

**Pre-existing test hangs (not caused by this session's work):**
- `test_parallel_runner.py::TestExecuteTaskWithSemaphore::test_semaphore_acquisition_failure_sets_blocked` — `threading.Semaphore(0).acquire()` blocks forever. Always exclude with `-k "not test_semaphore"`.
- `test_worktree.py::TestWorktreeManager` — fd exhaustion when run at end of 1700+ test suite. Passes in isolation.
- `test_cron_executor.py::TestTaskExecutorAgentTool` — LLM mock not isolating correctly; asserts on specific response string but actual LLM may be called. Pre-existing flakiness.

**`cron/api.py` must NOT have `from __future__ import annotations`** — breaks FastAPI `Request` type injection.

**`FileUserStore` (not `UserStore`). `update_user()` (not `upsert_user()`).** Both have caused bugs before.

---

## 6. Conventions In Play

- **Caveman mode active** — responses are terse, fragments OK. `stop caveman` to revert.
- **Imports are lazy throughout `cli.py`** — all heavy imports inside function bodies, not at module level. Keep this pattern for any new CLI subcommands.
- **Errors in auth helpers swallowed silently** — phases 2–4 degrade to no-op if session missing or vault unavailable. Never raise from `_record_run_cost()` or vault injection. This is intentional.
- **Commit after each phase; push after each session.** Already done — `5f69087` pushed.
- **Test with `source .venv/bin/activate && python -m pytest tests/ -q --tb=short -k "not test_semaphore and not test_worktree and not TestTaskExecutorAgentTool"`**
- **CLAUDE.md is the canonical architecture reference** — D-numbers, module docs, gotchas all live there. Update it when adding new modules or changing behavior.
- Version in `pyproject.toml` has been bumping; don't worry about bumping it explicitly — happens separately.

---

## 7. Open Questions

1. **Phase 5 (CLI API wrappers):** Do you want `orchid user list/invite`, `orchid scheduler list/run`, `orchid audit`? These are thin `httpx` wrappers around existing API endpoints — see `docs/cli-auth-plan.md` §Phase 5 for the full spec. Scope is ~100 lines.

2. **`orchid mcp` showing both project and catalog servers when logged in?** Currently with session, `orchid mcp ls` shows only catalog + private servers (`.orchid.yaml` project servers are hidden). Is this the desired behavior? Fix is low effort — merge server configs before connecting.

3. **The `test_cron_executor.py::TestTaskExecutorAgentTool` tests** are calling the actual LLM rather than hitting the mock. Worth fixing or just skip permanently?

4. **`/api/auth/me` doesn't return `budget_used_usd` or `budget_usd`.** `orchid whoami` only shows `user_id`, `username`, `role`, `email`. Should budget info be added to that endpoint?

---

## 8. Do Not Touch

- `orchid/web/server.py` — dead file, never loaded
- `orchid/interfaces/portal/vite.config.js` `base: '/app/'` — load-bearing
- `orchid/auth/jwt.py` — crypto settled
- `.claude/settings.local.json` — local harness config, never commit
- The cron executor's `_exec_local.cost_usd` thread-local pattern — budget recording in the cron path already works correctly; don't conflate it with the CLI path

---

## 9. Resume Command

> Read `HANDOFF.md`. CLI auth integration (phases 1–4) is complete at commit `5f69087` — `orchid login/logout/whoami` work, vault injection and budget recording fire on `--mode auto` and `--run-task`, `orchid mcp` uses per-user catalog when logged in. Next: either implement Phase 5 (optional CLI API wrappers — see `docs/cli-auth-plan.md`), or address one of the open questions in §7. Run tests with `source .venv/bin/activate && python -m pytest tests/ -q -k "not test_semaphore and not test_worktree and not TestTaskExecutorAgentTool"`. Do not touch `orchid/web/server.py`. Commit between phases; push after session.
