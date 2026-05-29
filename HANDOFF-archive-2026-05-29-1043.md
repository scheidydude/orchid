# HANDOFF.md
_Updated: 2026-05-27. Previous HANDOFF archived as `HANDOFF-archive-2026-05-10-0000.md`._

---

## 1. Mission

Orchid is a standalone AI agent orchestration framework. The last two sessions transformed it into a true **multi-user agentic OS** — all five phases from `docs/multiuser-proposal.md` are complete, stretch items done, and the post-v3 hardening pass is complete.

**286 new-feature tests pass at commit `81ba172`.**

---

## 2. Phase Map

| Phase | Feature | Commit | Tests |
|-------|---------|--------|-------|
| 1 | User Portal SPA (`/app/`) | `14b775a` | 87 |
| 2 | Credential Vault + Notifications + Admin Invite | `f76db1c` | +43 |
| 3 | MCP Catalog + per-user server access | `3e76d22` | +65 |
| 4 | Admin Console SPA (`/admin/`) | `6f4490a` | +9 |
| 5 | Budget enforcement + vault provider injection | `ae8dcc0` | +21 |
| Stretch | CPU budget, Task Monitor, System Config | `f30567a` | +12 |
| Post-v3 | Bot DMs, allow_user_projects, project registry, Postgres | `7cb35c0`–`81ba172` | +89 |

---

## 3. Current State

### Complete at `81ba172`

#### Post-v3 hardening (this session)

**Bot DM notifications (`orchid/auth/notifications.py`)**
- `dispatch_task_notification()` now sends real Telegram DMs and Slack DMs via `CentralBotManager`
- `get_bot_manager()` / `set_bot_manager()` singleton in `central_bot.py` (not `web_server.py` — avoids circular import)
- `CentralTelegramBot.send_dm(chat_id, text)` — `run_coroutine_threadsafe` on bot's event loop (thread-safe from cron)
- `CentralSlackBot.send_dm(user_id, text)` — sync `WebClient.conversations_open` + `chat_postMessage`
- `_format_dm_text(task_name, status, run_id, output)` — 500-char truncation, status emoji
- `set_bot_manager()` called in `web_server.py` after bot start; `set_bot_manager(None)` before stop

**`allow_user_projects` enforcement**
- `POST /api/projects` returns 403 for non-admin when `web.allow_user_projects` is False
- Admin always bypasses; flag check is before auth-scoped logic
- `CentralTelegramBot._cmd_new` — checks flag, replies error if disabled
- `CentralSlackBot._handle_new` — checks flag, responds error if disabled

**Project ownership registry (`orchid/projects/registry.py`)**
- `ProjectEntry` dataclass: `project_id`, `project_path`, `owner_id`, `created_at`
- `ProjectRegistry` — thread-safe JSON store at `~/.config/orchid/projects/registry.json`
- `user_project_base(uid)` → `~/.config/orchid/projects/{uid}/`
- `GET /api/projects` — filters by `User.projects` whitelist (D0060); admin sees all; `owner_id` in response
- `POST /api/projects` — user role → base_dir = `user_project_base(uid)`; records ownership; `owner_id` in response
- `_unregister_project()` calls `registry.unregister(pid)`
- `get_registry()` singleton; `reset_registry()` for tests

**PostgresUserStore (`orchid/auth/store_postgres.py`)**
- Full production PostgreSQL backend; `ThreadedConnectionPool`; `RealDictCursor`
- 6 tables: `orchid_users`, `orchid_refresh_tokens`, `orchid_api_keys`, `orchid_oauth_accounts`, `orchid_invites`, `orchid_scheduled_tasks`
- Idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` migrations for existing databases
- `get_store()` auto-selects `PostgresUserStore` when `ORCHID_AUTH_STORE_DSN` env var is set
- Dev DB: `postgresql://orchid:orchid_dev@localhost/orchid`
- Migration tool: `orchid migrate-to-postgres --dsn DSN [--dry-run]`
- 39 integration tests against live DB, all passing

#### Budget module (`orchid/budget/guard.py`) — from Phase 5

- **`BudgetGuard(owner_id)`** — `check()`/`record(usd)` for LLM spend; `check_cpu()`/`record_cpu(s)` for wall-clock time (daily UTC reset via `cpu_last_reset_date`); `remaining()` / `remaining_cpu()`
- **`vault_env_context(owner_id)`** — thread-local env injection; concurrent cron jobs don't bleed keys
- **`get_env(key)`** — thread-local-first drop-in for `os.environ.get()`
- **`_compute_anthropic_cost(model, input_tokens, output_tokens)`** — USD from prefix table

#### `orchid/orchid.defaults.yaml` — `multi_user` section
```yaml
multi_user:
  credential_encryption: fernet
  default_budget_usd: 0.0
  default_cpu_seconds: 0.0
  allow_user_mcp: true
  allow_user_projects: true
  mcp_catalog_path: ""
```

#### Admin Console SPA — 6 tabs

| Tab | Page | Key features |
|-----|------|-------------|
| Users | `Users.jsx` | Table + invite + edit + deactivate |
| MCP Catalog | `MCPCatalog.jsx` | CRUD + grant/revoke access |
| Audit Log | `AuditLog.jsx` | Paginated, filterable, expand detail |
| Quotas | `Quotas.jsx` | Inline-edit LLM+CPU budget; usage bars; reset button |
| Task Monitor | `TaskMonitor.jsx` | All users' runs; status badge; expandable output/error |
| System Config | `SystemConfig.jsx` | Toggle allow_user_mcp, allow_user_projects; default quotas |

---

### What's working since earlier phases

**Phase 3 — MCP Catalog:**
- `MCPServerEntry` + `MCPCatalogStore` at `~/.config/orchid/mcp_catalog.json`
- `UserMCPStore` per-user at `~/.config/orchid/users/{uid}/mcp_servers.json`
- `MCPManager.connect_for_user()` — merges catalog + private servers, injects vault creds
- Access control: `admin-only` > explicit `allowed_users` > `allowed_roles`

**Phase 2 — Credential Vault + Invite:**
- `VaultStore` — Fernet, HKDF per-user key (`ORCHID_VAULT_KEY`)
- Admin-invite flow: create inactive user → token → `POST /api/auth/invite/accept`
- `orchid/auth/notifications.py` — email live + Telegram/Slack now live via CentralBotManager

**Phase 1 — User Portal:**
- `/app/` SPA: Dashboard, Settings (profile, vault, notifications, API keys, MCP servers)
- Root redirect: admin → `/admin/`, user → `/app/`

---

## 4. Key Files Reference

```
orchid/
  auth/
    types.py            User dataclass (all fields including budget, cpu, scheduled_tasks)
    store.py            FileUserStore; get_store() auto-selects Postgres if ORCHID_AUTH_STORE_DSN set
    store_postgres.py   PostgresUserStore — full production backend
    audit.py            AuditAction constants
    jwt.py              hash_password, issue_access_token, …
    middleware.py       get_current_user, require_auth(role=), require_scope(scope=)
    notifications.py    dispatch_task_notification() — live email + bot DMs
    mailer.py           SMTP email via env vars
  budget/
    guard.py            BudgetGuard, vault_env_context, get_env, _compute_anthropic_cost
  vault/
    store.py            VaultStore, get_vault() singleton
    api.py              /api/user/credentials/* + /api/user/config/notifications
  mcp/
    catalog.py          MCPServerEntry, MCPCatalogStore, UserMCPStore
    catalog_api.py      register_admin_routes() + register_user_routes()
    manager.py          MCPManager + connect_for_user()
  cron/
    executor.py         TaskExecutor.execute() — vault inject → budget → dispatch → record
    store.py            TaskRunStore (append-only JSONL, 30-day prune)
    engine.py           CronEngine, APScheduler, get_engine() singleton
    api.py              /api/scheduler/* routes — NO from __future__ import annotations
  projects/
    __init__.py         (empty)
    registry.py         ProjectRegistry, ProjectEntry, user_project_base(), get_registry()
  interfaces/
    web_server.py       FastAPI create_app(); ALL web routes
    web/server.py       DEAD FILE — never loaded, never touch
    central_bot.py      CentralBotManager + get_bot_manager()/set_bot_manager() singleton
    telegram_central.py CentralTelegramBot + send_dm(chat_id, text)
    slack_central.py    CentralSlackBot + send_dm(user_id, text)
    portal/             User Portal SPA (base: /app/, port 5174)
    admin/              Admin Console SPA (base: /admin/, port 5175)
```

---

## 5. Gotchas & Hard-Won Knowledge

**`orchid/web/server.py` is a dead file.** All routes in `orchid/interfaces/web_server.py`.

**Portal `vite.config.js` `base: '/app/'` is load-bearing.** Do not change.

**`store.update_user()` not `store.upsert_user()`.** Class is `FileUserStore`.

**`_parse_user()` uses `dataclasses.fields(User)`.** Adding a field with a default auto-persists — no store changes needed.

**`cron/api.py` must NOT have `from __future__ import annotations`** — breaks FastAPI `Request` injection.

**`vault_env_context` uses thread-local, NOT `os.environ`.** `get_env()` in execution paths, not `os.environ.get()`.

**`get_bot_manager()` is in `central_bot.py`**, not `web_server.py`. Notifications import it lazily to avoid circular imports.

**`reset_registry()` in tests** that instantiate `ProjectRegistry` directly — the singleton persists across test functions.

**`PostgresUserStore` pool isn't closed automatically.** In tests, always use a fresh store per class; in production the pool lives for the process lifetime.

**`ORCHID_VAULT_KEY` rotation invalidates all user vaults.** `ORCHID_AUTH_STORE_DSN` change requires `orchid migrate-to-postgres`.

**Pre-existing test hangs** (not caused by any recent work):
- `test_parallel_runner.py::TestExecuteTaskWithSemaphore::test_semaphore_acquisition_failure_sets_blocked` — `threading.Semaphore(0)` acquire blocks forever; skip or fix with `timeout=`.
- `test_worktree.py::TestWorktreeManager` — errors at end of full 1700+ test suite due to fd exhaustion from accumulated `tmp_path` dirs; passes when run in isolation.

---

## 6. Do Not Touch

- `orchid/web/server.py` — dead-end, never loaded by `orchid serve`
- `orchid/interfaces/portal/vite.config.js` `base: '/app/'` — load-bearing
- `orchid/auth/jwt.py` — crypto params settled
- `.claude/settings.local.json` — local harness config, never commit

---

## 7. What's Left

| Item | Notes |
|------|-------|
| **Admin "view as user" mode** | Not implemented. Admin can visit `/app/` as themselves for debugging. Low priority. |
| **Fix `test_parallel_runner` hang** | `threading.Semaphore(0)` blocks forever — add `timeout=` or use `MagicMock`. |
| **Fix `test_worktree` fd exhaustion** | Use `--basetemp` flag or reduce `tmp_path` usage. Pre-existing, not blocking. |

---

## 8. Resume Command

> Read `HANDOFF.md`. Orchid multi-user OS + post-v3 hardening complete at commit `81ba172` — 286 new-feature tests pass. See §7 for remaining items. Do not touch `orchid/web/server.py` (dead-end). Do not change portal `vite.config.js` `base: '/app/'`. Commit between major changes. Push after each phase.
