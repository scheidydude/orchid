<!-- compressed 2026-05-27 -->

# CLAUDE.md — Orchid Framework (v3.0)

## Core
Standalone AI agent orchestration + multi-user agentic OS. Tool (`~/orchid/`) invokes external projects (`~/projects/<name>/`). Projects opt-in via `CLAUDE.md` + `tasks.md` + `.orchid.yaml`. V3 adds full multi-user support: per-user credential vault, MCP catalog, LLM+CPU budget enforcement, admin console SPA.

## Layout
`~/projects/<name>/.orchid/`: `decisions.json`, `session_logs/`, `chroma/`, `task_results.json`.
`~/.config/orchid/users/{user_id}/`: `credentials.json.enc`, `mcp_servers.json`.
`~/.config/orchid/`: `mcp_catalog.json`, `cron/runs.jsonl`, `audit/audit-YYYY-MM-DD.jsonl`.

## CLI
**Top-level flags** (all attach to `--project`, default cwd):

Run modes: `--mode auto|interactive` · `--code-model` · `--provider agent=name` · `--offline` · `--max-tasks N` · `--output-format stream-json` · `--trace`

Query/mutate: `--status` · `--recall "q"` · `--search "q"` · `--add-task "t"` · `--run-task T001` · `--check-providers`

V2 lifecycle: `--interactive` (planning loop) · `--phase` · `--artifacts` · `--approve [--auto]`

Checkpoints: `--list-checkpoints` · `--rewind CHECKPOINT_ID` · `--resume CHECKPOINT_ID`

Live agent: `--tail` · `--inject TEXT` · `--get-result TASK_ID`

**Subcommands:**
`orchid init <path> [--name] [--description] [--force]` — scaffold CLAUDE.md / tasks.md / .orchid.yaml
`orchid decide "Title" --decision "..." [--rationale]` — record architectural decision
`orchid new "<desc>" [--name] [--type ai|web|tool|game] [--no-interactive]` — create project + start interactive planning
`orchid serve [--watch-dir DIR] [--port 7842] [--telegram|--slack|--bots]` — unified persistent server
`orchid task add|done|block|cancel|skip [--id T001] [--type] [--priority] [--desc]` — task management
`orchid multi start|status|stop [--project P] [--workers N]` — alternate multi-project entry
`orchid login [--server URL] [--username]` — auth with server, save session to `~/.config/orchid/cli_session.json` (0600)
`orchid logout` — revoke refresh token server-side + delete session file
`orchid whoami` — show current user from `/api/auth/me`
`orchid mcp ls` / `orchid mcp call <tool> [--arg JSON]` — MCP tool inspect/call; uses `connect_for_user()` when logged in (per-user catalog ACLs), anonymous `connect()` otherwise
`orchid migrate-to-postgres --dsn DSN [--dry-run]` — migrate FileUserStore → Postgres
`orchid hooks` — manage CLI hooks (registered from `hooks_cli.py`)

*Deprecated (still functional):* `orchid telegram|slack|web` → use `orchid serve --telegram/--slack`.

**Auth (D0066):** When logged in (`cli_session.json` present): `--mode auto` and `--run-task` wrap task execution in `vault_env_context(user_id)` so vault credentials are available to providers, and call `BudgetGuard.check()` before / `BudgetGuard.record()` after. No session = silent fallback to anonymous mode (no vault, no budget tracking, no MCP catalog ACLs). Scheduler, audit log, and user management remain web/API only.

## Tasks (`tasks.md`)
`- [ ] **T001** Title \`type:code_generate\` \`p1\` \`needs:T002\` \`model:claude\``.
Skip: `- [~] **T003**`. Rollup: `- [ ] **T099** \`type:rollup\` \`rollup:T090,T091\` \`output:FILE.md\``.

## Tool Calls (ReAct)
`Action: <name>\nAction Input: <json>`. Actions: `read_file`, `list_dir`, `bash`, `write_file` (replace), `append_file` (add), `delegate`.

## Architecture Decisions
**D0001** File-state. **D0002** 2-tier routing (Claude/llama). **D0003** ReAct text. **D0004** Interface-agnostic. **D0005** 3-layer config. **D0006** Standalone runtime. **D0007** Embed Chroma. **D0008** Embed: llama→ST. **D0009** Auto-embed/recall. **D0010** Search: SearXNG→Brave. **D0011** Extract: trafilatura. **D0012** Delegate depth 3. **D0013** Sub-context. **D0014** Telegram logic. **D0015** User whitelist. **D0016** Model routing. **D0017** Task deps. **D0018** Live log. **D0019** Inject queue. **D0020** Telegram notify. **D0021** Process parallelism. **D0022** Claude sem. **D0024** Slack Socket. **D0025** Slack threads. **D0026** Shared Runner. **D0027** Web FastAPI/React. **D0028** React dist. **D0029** Traefik TLS. **D0030** ProviderBase ABC; resolution order: CLI > user vault > project providers.<agent> > project providers.task_types.<type> > task annotation > env > type/agent defaults. **D0031** Shared backends. **D0032** Provider check. **D0033** Watchdog. **D0034** Orchid serve. **D0035** AgentManager. **D0036** XDG config. **D0037** Rollup Claude. **D0038** TaskResultStore. **D0039** Shell allowlist. **D0040** Tiktoken chunking. **D0041** V2 Lifecycle. **D0042** Strategic agents. **D0043** Gates. **D0044** Machine profile. **D0045** Web Planning. **D0046** WS Stream. **D0047** Wizard. **D0048** Prompt cache. **D0049** KV cache. **D0050** CentralBot. **D0051** Telegram state. **D0052** Slack map. **D0053** Bot serve. **D0054** JWT auth: HS256 access tokens (15 min) + opaque refresh tokens (30 days, argon2-hashed, rotated on use); HttpOnly cookies for web, Bearer header for API/mobile. **D0055** Argon2id passwords: time=3, mem=64MB, par=4 (OWASP); no plaintext ever stored. **D0056** API keys: `ok_{key_id}.{secret}` format; argon2-hashed secret, O(1) lookup; scopes list; `require_scope()` FastAPI dependency; JWT sessions unrestricted. **D0057** OIDC provider registry: `GenericOIDCProvider` fetches discovery doc, caches metadata; `GoogleOIDCProvider` + `EntraOIDCProvider` subclass; `ProviderRegistry.from_config()` loads from `auth.providers` YAML; account linking by email. **D0058** PKCE S256 mobile flow: `code_challenge` stored in OAuth state; server-side `_verify_pkce_s256()` (timing-safe) before provider exchange; `POST /api/auth/oauth/{p}/token` returns JSON tokens (no cookies); `code_verifier` forwarded to provider. **D0059** Audit log: append-only JSONL, daily rotation (`audit-YYYY-MM-DD.jsonl`), archived forever, thread-safe; action constants in `AuditAction`; fire-and-forget `_log_audit()` never raises. **D0060** Per-user project scoping: `User.projects` list; empty = unrestricted; admin always bypasses; `_check_project_access()` raises 403. **D0061** Cron scheduled tasks: `User.scheduled_tasks: list[dict]` (no circular import); `TaskRunStore` append-only JSONL `~/.config/orchid/cron/runs.jsonl`, 30-day prune; `TaskExecutor` dispatches `agent_prompt`/`mcp_tool`/`shell`, always returns `TaskRun`, never raises; `CronEngine` wraps APScheduler `BackgroundScheduler` (UTC), `get_engine()` singleton; `register_routes()` installs `/api/scheduler/*`; `from __future__ import annotations` must NOT appear in `cron/api.py`. **D0062** Per-user credential vault: Fernet-encrypted JSON at `~/.config/orchid/users/{uid}/credentials.json.enc`; key = HKDF-SHA256(ORCHID_VAULT_KEY, salt=b"orchid-vault-v1", info=user_id.encode())[:32]; rotating ORCHID_VAULT_KEY invalidates all vaults (document this). **D0063** MCP catalog: `MCPServerEntry` at `~/.config/orchid/mcp_catalog.json` (separate from users.json); access control: admin-only scope > explicit allowed_users > allowed_roles; `connect_for_user()` coexists with `connect()` — zero breaking changes. **D0064** Budget enforcement: `User.budget_used_usd` in users.json (auto-persisted via `dataclasses.fields`); `BudgetGuard` checks + records; `vault_env_context` uses thread-local overrides (not os.environ) for concurrent cron job isolation; CPU budget = wall-clock time, lazy daily reset via `cpu_last_reset_date` string. **D0066** CLI auth: `cli_session.json` (0600) stores user_id/role/tokens/server_url/issued_at; `load_cli_session()` for local ops (vault/budget), `get_valid_session()` auto-refreshes for server calls; phases 2–4 work with no server connection (all local file ops). **D0065** Multi-user SPAs: User Portal `/app/` (port 5174, `base: '/app/'`); Admin Console `/admin/` (port 5175, `base: '/admin/'`); both served by same FastAPI; root redirect conditioned on `_ADMIN_DIST_DIR.exists()`; admin visits `/app/` to debug user view.

## Auth Module (`orchid/auth/`)
`types.py` — `User` (fields: `user_id`, `username`, `email`, `role`, `is_active`, `projects`, `api_keys`, `budget_usd`, `budget_used_usd`, `cpu_budget_seconds`, `cpu_used_seconds`, `cpu_last_reset_date`, `password_hash`, `scheduled_tasks`, `notification_config`), `RefreshToken`, `ApiKey`, `OAuthAccount`, `InviteToken`, `AuditEvent`.
`store.py` — `FileUserStore` (class name; import as `from orchid.auth.store import FileUserStore`): thread-safe JSON-backed; `_parse_user()` uses `dataclasses.fields(User)` — new User fields auto-persist without store changes. `get_store()` singleton.
`base.py` — `BaseUserStore` ABC: abstract methods for users, tokens, API keys, OAuth accounts, scheduled tasks, invites.
`jwt.py` — `hash_password`, `verify_password`, `issue_access_token`, `verify_access_token`, `issue_refresh_token`, `verify_refresh_token`, `issue_api_key`, `verify_api_key`.
`middleware.py` — `get_current_user`, `get_optional_user`, `require_auth(role=)`, `require_scope(scope=)`.
`audit.py` — `AuditStore` (JSONL, daily rotation), `AuditAction` constants (LOGIN, LOGIN_FAILED, LOGOUT, REGISTER, TOKEN_REFRESHED, API_KEY_CREATED/REVOKED, OAUTH_LOGIN, TASK_RUN, PROJECT_ACCESS_DENIED, USER_UPDATED, USER_DEACTIVATED, SCHEDULED_TASK_RUN/FAILED, INVITE_SENT/ACCEPTED, CREDENTIAL_UPDATED/DELETED, NOTIFICATION_CONFIG_UPDATED, MCP_SERVER_CREATED/UPDATED/DELETED, MCP_ACCESS_GRANTED/REVOKED, USER_MCP_SERVER_ADDED/DELETED, BUDGET_EXCEEDED, BUDGET_RESET).
`notifications.py` — `dispatch_task_notification()`: email (live via SMTP), Telegram/Slack (logged stubs pending bot wiring).
`mailer.py` — SMTP via `SMTP_HOST/PORT/USER/PASSWORD/FROM/USE_SSL` env vars; no-op if unconfigured.
`providers/` — `OIDCProvider` ABC, `GenericOIDCProvider`, `GoogleOIDCProvider`, `EntraOIDCProvider`, `ProviderRegistry`.

## Vault Module (`orchid/vault/`)
`store.py` — `VaultStore`: Fernet-encrypted per-user credential store; `list_keys(uid)`, `get(uid, key)`, `set(uid, key, value)`, `delete(uid, key)`, `delete_all(uid)`. `get_vault()` singleton. `reset_vault()` for tests. Raises `RuntimeError` if `ORCHID_VAULT_KEY` not set.
`api.py` — `register_routes(app)`: `/api/user/credentials/*` + `/api/user/config/notifications`. Local imports only.

## Budget Module (`orchid/budget/`)
`guard.py`:
- `BudgetGuard(owner_id, store=None)`: `check()` raises `BudgetExceededError` (0 = unlimited); `record(usd)` increments `budget_used_usd`; `remaining()` → float|None. CPU: `check_cpu()` enforces `cpu_budget_seconds` daily wall-clock cap with auto-reset; `record_cpu(s)` increments `cpu_used_seconds`; `remaining_cpu()` → float|None.
- `vault_env_context(owner_id, vault_store=None)` — context manager; injects vault keys matching `_PROVIDER_ENV_VARS` as **thread-local** overrides (not `os.environ`); use with concurrent cron jobs.
- `get_env(key, default=None)` — reads thread-local overrides first, then `os.environ`; use instead of `os.environ.get()` in task execution paths.
- `_compute_anthropic_cost(model, input_tokens, output_tokens)` — USD estimate from `_ANTHROPIC_PRICING` prefix table.

## MCP Catalog (`orchid/mcp/`)
`catalog.py` — `MCPServerEntry` dataclass; `MCPCatalogStore` at `~/.config/orchid/mcp_catalog.json`; `UserMCPStore` per-user at `~/.config/orchid/users/{uid}/mcp_servers.json`; `get_catalog()` / `reset_catalog()` singletons.
`catalog_api.py` — `register_admin_routes(app)` + `register_user_routes(app)`. Local imports only.
`manager.py` — `MCPManager`: existing `connect()` + new `connect_for_user(user_id, user_role, catalog_store, vault_store, users_dir)` (coexist; no breaking changes).

## Cron Module (`orchid/cron/`)
`types.py` — `ScheduledTask`, `TaskRun`.
`store.py` — `TaskRunStore`: `get_runs(task_id, owner_id, limit)` newest-first; reads `~/.config/orchid/cron/runs.jsonl`.
`executor.py` — `TaskExecutor.execute(task_dict, owner_id)`: wraps dispatch with `vault_env_context` → `guard.check()` + `guard.check_cpu()` → dispatch → `guard.record(cost)` + `guard.record_cpu(elapsed)` in finally. Cost accumulated via `_exec_local.cost_usd` thread-local. Never raises.
`engine.py` — `CronEngine`, APScheduler, `get_engine()` singleton, `reset_engine()` for tests.
`api.py` — `register_routes(app)`: NO `from __future__ import annotations` (breaks FastAPI `Request` injection).

## Auth + Admin Endpoints
```
POST /api/auth/register           hash password, store user
POST /api/auth/login              verify hash, issue JWT + refresh, set HttpOnly cookies
POST /api/auth/refresh            rotate refresh token, issue new pair
POST /api/auth/logout             revoke refresh token, clear cookies
GET  /api/auth/me                 current user info
POST /api/auth/token              verify JWT, return user_id
GET  /api/auth/users              list users + budget/cpu fields (admin)
PUT  /api/auth/users/{id}         update role/projects/is_active/email/budget_usd/cpu_budget_seconds (admin)
DELETE /api/auth/users/{id}       deactivate + revoke sessions (admin, preserves record)
POST /api/admin/invite            create inactive user + InviteToken, returns invite_url (admin)
GET  /api/auth/invite/{token_id}  validate invite token (public)
POST /api/auth/invite/accept      accept invite, set password, activate user (public)
POST /api/auth/apikeys            create API key (secret shown once)
GET  /api/auth/apikeys            list keys (no secrets)
DELETE /api/auth/apikeys/{id}     revoke key
GET  /api/auth/oauth/providers    list configured SSO providers
GET  /api/auth/oauth/{p}/start    redirect to provider (PKCE optional)
GET  /api/auth/oauth/{p}/callback web callback
POST /api/auth/oauth/{p}/token    mobile PKCE exchange → JSON tokens
GET  /api/audit                   paginated audit log (admin, filter user_id/action)
GET  /api/user/credentials        list vault key names (no values)
PUT  /api/user/credentials/{key}  store encrypted value
DELETE /api/user/credentials/{key}
GET  /api/user/config/notifications
PUT  /api/user/config/notifications
GET  /api/user/mcp/servers        {shared:[...], private:[...]}
POST /api/user/mcp/servers        add private MCP server (gated by web.allow_user_mcp)
DELETE /api/user/mcp/servers/{id}
GET  /api/admin/mcp/catalog
POST /api/admin/mcp/catalog
GET  /api/admin/mcp/catalog/{id}
PUT  /api/admin/mcp/catalog/{id}
DELETE /api/admin/mcp/catalog/{id}
PUT  /api/admin/mcp/catalog/{id}/grant    {role:} or {user_id:}
PUT  /api/admin/mcp/catalog/{id}/revoke
POST /api/admin/users/{id}/budget/reset   reset budget_used_usd to 0
GET  /api/admin/runs              paginated task runs all users (filter owner_id, status)
GET  /api/admin/config            current multi_user.* + web.allow_* config values
PUT  /api/admin/config            write allowlisted keys to ~/.config/orchid/config.yaml
PUT  /api/auth/me/password        change own password (verify current, 8-char min)
GET  /api/scheduler/tasks         list user's scheduled tasks
POST /api/scheduler/tasks         create scheduled task
GET  /api/scheduler/tasks/{id}
PUT  /api/scheduler/tasks/{id}
DELETE /api/scheduler/tasks/{id}
POST /api/scheduler/tasks/{id}/run  trigger immediately
GET  /api/scheduler/tasks/{id}/runs run history
GET  /api/scheduler/runs            all runs for current user
```

## Required Env Vars
`JWT_SECRET` — required; never commit.
`ORCHID_VAULT_KEY` — required for credential vault; independent from JWT_SECRET (separate rotation).
`ANTHROPIC_API_KEY` — required for Claude provider.
`GOOGLE_CLIENT_ID/SECRET`, `AZURE_TENANT_ID/CLIENT_ID/SECRET` — optional OAuth.
`SMTP_HOST/PORT/USER/PASSWORD/FROM/USE_SSL` — optional email notifications.
`TELEGRAM_BOT_TOKEN`, `SLACK_BOT_TOKEN` — optional bot interfaces.
`ORCHID_AUTH_STORE_DSN` — optional; if set, `get_store()` uses `PostgresUserStore` instead of `FileUserStore`. Format: `postgresql://user:pass@host/db`. Requires `psycopg2-binary` (`uv pip install 'orchid[postgres]'`).

## Current State
**V3.0 Complete + post-v3 hardening. 286 new-feature tests passing at commit `5541403`.**
Multi-user OS phases all done + post-v3 items complete. Test breakdown: 87 (Phases 1–2) + 65 (Phase 3 MCP) + 9 (Phase 4 admin API) + 33 (Phase 5 budget + stretch) + 19 (bot DM wiring) + 7 (allow_user_projects) + 21 (project registry) + 39 (PostgresUserStore) + 6 (misc) = 286.

**Post-v3 items (commit `7cb35c0`–`5541403`):**
*   **Bot DM notifications**: `CentralBotManager.send_telegram_dm()` / `send_slack_dm()`; `dispatch_task_notification()` wired to live bots via `get_bot_manager()` singleton (no circular import). `set_bot_manager()` called in `web_server.py` after bot start.
*   **`allow_user_projects` flag**: `web.allow_user_projects` config key; `POST /api/projects` returns 403 for non-admin when disabled; admin bypasses; Telegram `_cmd_new` + Slack `_handle_new` both check flag and reply with error.
*   **Project ownership registry**: `orchid/projects/registry.py` — JSON-backed `ProjectRegistry`; `user_project_base(uid)` → `~/.config/orchid/projects/{uid}/`; `GET /api/projects` filters by `User.projects` whitelist (D0060); `POST /api/projects` records ownership + routes user projects to user namespace; `_unregister_project()` calls `registry.unregister()`.
*   **PostgresUserStore** (`orchid/auth/store_postgres.py`): full production backend; `ThreadedConnectionPool`; all 6 tables + idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` migrations; `get_store()` auto-selects when `ORCHID_AUTH_STORE_DSN` set. `orchid migrate-to-postgres --dsn DSN [--dry-run]` copies FileUserStore → Postgres. Tested against live DB (postgresql://orchid:orchid_dev@localhost/orchid).

*   **Multi-user P1** User Portal SPA `/app/`: dashboard, settings (vault, notifications, API keys, MCP servers), accept-invite.
*   **Multi-user P2** Credential vault (Fernet/HKDF), notification config, admin-invite flow, `InviteToken`.
*   **Multi-user P3** MCP catalog + `UserMCPStore` + `MCPManager.connect_for_user()`.
*   **Multi-user P4** Admin Console SPA `/admin/`: Users, MCP Catalog, Audit Log, Quotas, Task Monitor, System Config.
*   **Multi-user P5** `BudgetGuard` (LLM + CPU), `vault_env_context` (thread-local), budget reset endpoint.
*   **Stretch** CPU daily cap with auto-reset, Task Monitor page (`GET /api/admin/runs`), System Config page (`GET/PUT /api/admin/config`), `multi_user` section in `orchid.defaults.yaml`.
*   Earlier: T051 shell allowlist, T053 V2 lifecycle, T054/55 web planning/streaming, T056 prompt cache, T061 CentralBot, T068 systemd, T285–T297 cron engine.

**Known pre-existing test issues (not from post-v3 work):**
- `test_parallel_runner.py::TestExecuteTaskWithSemaphore::test_semaphore_acquisition_failure_sets_blocked` — hangs forever; `threading.Semaphore(0)` acquire never unblocks.
- `test_worktree.py::TestWorktreeManager` — ERRORs when run at end of full suite (fd exhaustion from 1700+ accumulated tmp_path dirs); passes when run in isolation.

## Tooling
Prefer `codeindex` CLI via Bash over MCP tools for symbol lookup and indexing operations.

## Install
`uv venv && uv pip install -e ".[dev]"`. Env: `~/.config/orchid/.env`. `ANTHROPIC_API_KEY` + `JWT_SECRET` + `ORCHID_VAULT_KEY` required for full multi-user mode.

## Critical Gotchas
- `orchid/web/server.py` is a **dead file** — never loaded by `orchid serve`. All routes in `orchid/interfaces/web_server.py`.
- `orchid/interfaces/portal/vite.config.js` `base: '/app/'` is load-bearing — do not change.
- `cron/api.py` must NOT have `from __future__ import annotations` — breaks FastAPI `Request` injection.
- `FileUserStore` (not `UserStore`). `update_user()` (not `upsert_user()`).
- New `User` fields auto-persist — `_parse_user()` uses `dataclasses.fields(User)`.
- `vault_env_context` uses thread-local, NOT `os.environ`. `get_env()` in execution paths, not `os.environ.get()`.
- `PostgresUserStore` uses `ThreadedConnectionPool`; `ORCHID_AUTH_STORE_DSN` rotation requires `orchid migrate-to-postgres` to re-import data if schema is fresh. `ORCHID_VAULT_KEY` rotation invalidates all user vaults — document in ops runbook.
- `orchid/projects/registry.py` singleton: call `reset_registry()` in tests that create `ProjectRegistry` directly.
- `get_bot_manager()` / `set_bot_manager()` singleton in `central_bot.py` — not `web_server.py` (avoids circular import).
- `ORCHID_VAULT_KEY` rotation invalidates all user vaults — document in ops runbook.
- `cli_session.json` stores `user_id` + `role` in plaintext — phases 2–4 read these locally without contacting the server. Tokens are only needed for `whoami` (server call). `load_cli_session()` for local ops; `get_valid_session()` for server calls (auto-refresh).
