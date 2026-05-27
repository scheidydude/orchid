# HANDOFF.md
_Written: 2026-05-27. Previous HANDOFF archived as `HANDOFF-archive-2026-05-10-0000.md`._

---

## 1. Mission

Orchid is a standalone AI agent orchestration framework. This session transformed it into a true **multi-user agentic OS**. All five phases from `docs/multiuser-proposal.md` are complete, plus stretch items (CPU budget, Task Monitor, System Config page).

**All 158 tests pass at commit `f30567a`.**

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

---

## 3. Current State

### Complete at `f30567a`

#### Budget module (`orchid/budget/guard.py`)

- **`BudgetGuard(owner_id, store=None)`** — check/record/remaining for LLM spend:
  - `check()` raises `BudgetExceededError` when `budget_used_usd >= budget_usd` (0 = unlimited)
  - `record(cost_usd)` — increments `User.budget_used_usd`, persists via `store.update_user()`
  - `remaining()` → float or None (unlimited)
- **CPU budget methods** (same class):
  - `check_cpu()` — compares `cpu_used_seconds` vs `cpu_budget_seconds`; auto-resets counter at UTC midnight via `cpu_last_reset_date` field
  - `record_cpu(seconds)` — increments `User.cpu_used_seconds`, auto-resets if new day
  - `remaining_cpu()` → float or None (unlimited)
- **`vault_env_context(owner_id, vault_store=None)`** — thread-local env override; injects vault keys that match `_PROVIDER_ENV_VARS` (ANTHROPIC_API_KEY etc.); concurrent cron jobs don't bleed keys
- **`get_env(key, default=None)`** — drop-in for `os.environ.get()` that checks thread-local overrides first; used in `_run_agent_tool` for Anthropic client
- **`_compute_anthropic_cost(model, input_tokens, output_tokens)`** — USD estimate from `_ANTHROPIC_PRICING` prefix table
- **`BudgetExceededError(limit, used)`** — carries `.limit` and `.used` attributes

#### `orchid/auth/types.py` — `User` new fields
```python
budget_used_usd: float = 0.0       # cumulative LLM spend; auto-persisted
cpu_used_seconds: float = 0.0      # wall-clock seconds used today
cpu_last_reset_date: str = ""      # "YYYY-MM-DD" UTC — reset sentinel
```
(All auto-serialised via `dataclasses.fields(User)` in `_parse_user()`.)

#### `orchid/cron/executor.py` — execution flow (Phase 5+)
```
execute(task_dict, owner_id):
  with vault_env_context(owner_id):          # inject provider keys
    guard.check()                            # LLM budget
    guard.check_cpu()                        # CPU budget (daily reset)
    wall_start = time.monotonic()
    dispatch_fn(config)                      # run the task
    finally:
      guard.record(cost_usd)                 # LLM cost from _exec_local.cost_usd
      guard.record_cpu(elapsed)              # wall time
```
`BudgetExceededError` → `run.status = "failure"`, run returned normally (never raises).

#### New admin endpoints (`web_server.py`)

| Endpoint | Description |
|----------|-------------|
| `POST /api/admin/users/{id}/budget/reset` | Reset `budget_used_usd` to 0; audit-logged |
| `GET /api/admin/runs` | Paginated task runs all users; filter `owner_id`, `status` |
| `GET /api/admin/config` | Current `multi_user.*` + `web.allow_*` config values |
| `PUT /api/admin/config` | Write allowlisted keys to `~/.config/orchid/config.yaml`; invalidates in-memory config |

`GET /api/auth/users` now includes `budget_used_usd`, `cpu_used_seconds`.

#### `orchid/orchid.defaults.yaml` — new `multi_user` section
```yaml
multi_user:
  credential_encryption: fernet
  default_budget_usd: 0.0
  default_cpu_seconds: 0.0
  allow_user_mcp: true
  allow_user_projects: true
  mcp_catalog_path: ""
```

#### Admin Console SPA — now 6 tabs

| Tab | Page | Key features |
|-----|------|-------------|
| Users | `Users.jsx` | Table + invite + edit + deactivate |
| MCP Catalog | `MCPCatalog.jsx` | CRUD + grant/revoke access |
| Audit Log | `AuditLog.jsx` | Paginated, filterable, expand detail |
| Quotas | `Quotas.jsx` | Inline-edit LLM+CPU budget; usage bars; reset button; CPU today column |
| Task Monitor | `TaskMonitor.jsx` | All users' runs; status badge; duration; expandable output/error |
| System Config | `SystemConfig.jsx` | Toggle allow_user_mcp, allow_user_projects; edit default quotas |

#### `orchid/auth/audit.py` — new constants
```python
BUDGET_EXCEEDED = "budget_exceeded"
BUDGET_RESET    = "budget_reset"
# Phase 3 (already in codebase):
MCP_SERVER_CREATED / UPDATED / DELETED
MCP_ACCESS_GRANTED / REVOKED
USER_MCP_SERVER_ADDED / DELETED
```

---

### What's working since earlier phases

**Phase 3 — MCP Catalog (`orchid/mcp/catalog.py`, `catalog_api.py`):**
- `MCPServerEntry` + `MCPCatalogStore` at `~/.config/orchid/mcp_catalog.json`
- `UserMCPStore` per-user at `~/.config/orchid/users/{uid}/mcp_servers.json`
- `MCPManager.connect_for_user()` — merges catalog + private servers, injects vault creds
- Access control: `admin-only` > explicit `allowed_users` > `allowed_roles`
- Admin routes: `GET/POST /api/admin/mcp/catalog`, `PUT/DELETE`, grant/revoke
- User routes: `GET /api/user/mcp/servers`, `POST/DELETE`
- Portal `UserSettings.jsx`: MCPServers section

**Phase 2 — Credential Vault + Invite:**
- `VaultStore` — Fernet, HKDF per-user key (`ORCHID_VAULT_KEY`)
- `GET/PUT/DELETE /api/user/credentials/{key}` + notifications config API
- Admin-invite flow: create inactive user → token → `POST /api/auth/invite/accept`
- Portal `AcceptInvite` component (detects `?invite_id=&invite_token=` in URL)
- `orchid/auth/notifications.py` — email (live) + Telegram/Slack (logged stubs)

**Phase 1 — User Portal:**
- `/app/` SPA: Dashboard, Settings (profile, vault, notifications, API keys, MCP servers)
- `PUT /api/auth/me/password` — verify current pw, 8-char min
- Root redirect: admin → `/admin/`, user → `/app/`

---

## 4. Architecture Decisions

**D0054–D0060** — JWT, argon2, API keys, OIDC, PKCE, audit log, project scoping. See `CLAUDE.md`.

**Phase 5 decisions:**

**`budget_used_usd` in `users.json` (not separate ledger JSONL)**
— Simpler; `_parse_user()` auto-handles via `dataclasses.fields(User)`. No new store abstraction needed.

**Thread-local env overrides (not `os.environ`) for vault injection**
— APScheduler thread pool runs concurrent jobs. `os.environ` is process-global; patching it races. `threading.local()` is per-thread, zero-contention.

**CPU budget = wall-clock time of task execution**
— Easy to measure (`time.monotonic()`). Doesn't require OS-level CPU accounting. Good enough for rate-limiting runaway tasks.

**`cpu_last_reset_date` on User (not a cron job)**
— Daily reset is lazy: checked at `check_cpu()` / `record_cpu()` call time, not at midnight. Zero infrastructure. If a user runs no tasks, nothing happens.

**System Config writes to `~/.config/orchid/config.yaml`**
— Orchid's user-level override file (D0036). Only allowlisted keys can be written (`_ALLOWED_MU_KEYS`, `_ALLOWED_WEB_KEYS`). Invalidates in-memory `_config` singleton after write so next `get()` reflects change without restart.

---

## 5. Key Files Reference

```
orchid/
  auth/
    types.py           User dataclass (budget_usd, budget_used_usd, cpu_*)
    audit.py           AuditAction constants
    store.py           FileUserStore, get_store() singleton, _parse_user()
    jwt.py             hash_password, issue_access_token, …
    middleware.py      get_current_user, require_auth(role=), require_scope(scope=)
    notifications.py   dispatch_task_notification() — email live, TG/Slack stubs
    mailer.py          SMTP email via env vars; graceful no-op if unconfigured
  budget/
    guard.py           BudgetGuard, vault_env_context, get_env, _compute_anthropic_cost
  vault/
    store.py           VaultStore, get_vault() singleton; HKDF per-user Fernet key
    api.py             /api/user/credentials/* + /api/user/config/notifications
  mcp/
    catalog.py         MCPServerEntry, MCPCatalogStore, UserMCPStore
    catalog_api.py     register_admin_routes() + register_user_routes()
    manager.py         MCPManager + connect_for_user()
  cron/
    executor.py        TaskExecutor.execute() — vault inject → budget → dispatch → record
    store.py           TaskRunStore (append-only JSONL, 30-day prune)
    engine.py          CronEngine, APScheduler, get_engine() singleton
    api.py             /api/scheduler/* routes
  interfaces/
    web_server.py      FastAPI create_app(); ALL web routes; do not confuse with…
    web/server.py      DEAD FILE — never loaded, never touch
    portal/            User Portal SPA (base: /app/, port 5174)
    admin/             Admin Console SPA (base: /admin/, port 5175)
      src/pages/
        Users.jsx
        MCPCatalog.jsx
        AuditLog.jsx
        Quotas.jsx
        TaskMonitor.jsx
        SystemConfig.jsx
```

---

## 6. Gotchas & Hard-Won Knowledge

**`orchid/web/server.py` is a dead file.** Never loaded by `orchid serve`. All routes are in `orchid/interfaces/web_server.py`. Do not add routes to the dead file.

**Portal `vite.config.js` `base: '/app/'` is load-bearing.** Do not change it. Admin has its own `vite.config.js` with `base: '/admin/'`.

**`store.update_user()` not `store.upsert_user()`.** Class is `FileUserStore`, not `UserStore`. Import path: `from orchid.auth.store import FileUserStore`.

**`_parse_user()` uses `dataclasses.fields(User)`.** Adding a field to `User` with a default is sufficient for it to be persisted and loaded automatically. No store code changes needed.

**`vault_env_context` must wrap the entire task dispatch**, not just the Anthropic client init. The thread-local overrides are read by `get_env()` at API call time inside `_run_agent_tool`.

**`cpu_last_reset_date` comparisons use `datetime.now(UTC).strftime("%Y-%m-%d")`.** The `_today()` staticmethod is on `BudgetGuard`. The date stored in users.json is a plain string — no timezone parsing needed.

**`PUT /api/admin/config` allowlists keys.** Unknown keys in the request body are silently dropped — no 400. Check `updated` in the response to see what actually changed.

**`GET /api/admin/runs` reads real `~/.config/orchid/cron/runs.jsonl`** — in tests, patch `orchid.cron.store.TaskRunStore` with a store pointing at `tmp_path`.

**Admin SPA `dist/` not committed.** Build before deployment:
```bash
cd orchid/interfaces/admin && npm install && npm run build
```
Dev server: port 5175.

**Root redirect is conditional on `_ADMIN_DIST_DIR.exists()`** — admin → `/admin/` only after building. Otherwise falls through to old power-user SPA.

**`AuditLog.jsx` and `TaskMonitor.jsx` use `<>` (React fragment shorthand) inside `.map()`.** If React warns about missing keys, switch to `<React.Fragment key={...}>`.

---

## 7. Do Not Touch

- `orchid/web/server.py` — dead-end, never loaded by `orchid serve`
- `orchid/interfaces/web_ui/` — existing power-user SPA, untouched
- `orchid/auth/jwt.py` — crypto params settled
- `orchid/interfaces/portal/vite.config.js` `base: '/app/'` — load-bearing
- `.claude/settings.local.json` — local harness config, never commit
- `docs/multiuser-proposal.md` decisions section — resolved

---

## 8. What's Left (not started)

| Item | Notes |
|------|-------|
| **Project namespace** | `registry.list_projects(user_id=...)`, user-owned project paths `~/.config/orchid/projects/{uid}/`. Separate initiative — touches project runner, CLI. |
| **Telegram/Slack notification wiring** | Stubs in `orchid/auth/notifications.py`. Wire through `CentralBotManager`. |
| **Postgres migration** | Replace `FileUserStore` with `PostgresUserStore`. Groundwork exists (ABC). |
| **`allow_user_projects` enforcement** | Flag written to config but not yet checked in project creation paths. |
| **Admin "view as user" mode** | Proposal mentioned it; not implemented. Admin can visit `/app/` as themselves. |

---

## 9. Resume Command

> Read `HANDOFF.md`. Orchid multi-user OS is complete (all 5 phases + stretch items) at commit `f30567a` — 158 tests pass. Remaining work is in "What's Left" section. Do not touch `orchid/web/server.py` (dead-end). Do not change portal `vite.config.js` `base: '/app/'`. Commit between major changes. Push after each phase.
