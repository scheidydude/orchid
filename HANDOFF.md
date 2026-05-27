# HANDOFF.md
_Written: 2026-05-27. Previous HANDOFF archived as `HANDOFF-archive-2026-05-10-0000.md`._

---

## 1. Mission

Orchid is a standalone AI agent orchestration framework. This session transformed it into a true **multi-user agentic OS**. Phases 1, 2, and 3 are complete. Next is Phase 4: Admin Console SPA (`/admin`).

---

## 2. Current State

### What's working and verified at commit `3e76d22`

**Phase 3 — complete at `3e76d22`:**

*MCP catalog (`orchid/mcp/catalog.py`):*
- `MCPServerEntry` dataclass — `server_id`, `name`, `transport`, `config`, `scope`, `allowed_roles`, `allowed_users`, `requires_credential`
- `MCPCatalogStore` — thread-safe JSON-backed; `~/.config/orchid/mcp_catalog.json`; CRUD + `grant_access()`/`revoke_access()` + `get_servers_for_user(user_id, role)`
- Access control: `admin-only` scope → admin only; explicit `allowed_users` beats role; role fallback via `allowed_roles`; `shared`/`private` scopes
- `get_catalog()` singleton + `reset_catalog()` for tests

*Per-user private MCP servers (`UserMCPStore`):*
- `~/.config/orchid/users/{user_id}/mcp_servers.json` — JSON array of server configs
- `add_server()` auto-assigns `server_id` if missing; `delete_server()`; isolated per user

*`MCPManager.connect_for_user()`:*
- New method in `orchid/mcp/manager.py`; coexists with `connect()` (zero breaking changes)
- Merges catalog servers (filtered by access) + user's private servers
- Injects vault credentials: `stdio` → `env[key]`, `http` → `headers["Authorization"]`
- Catalog server takes precedence on `server_id` clash with private server
- Vault unavailable → logs warning, server still included (no crash)

*Admin API (`orchid/mcp/catalog_api.py`):*
- `GET/POST /api/admin/mcp/catalog`
- `GET/PUT/DELETE /api/admin/mcp/catalog/{server_id}`
- `PUT /api/admin/mcp/catalog/{server_id}/grant` — role or user_id
- `PUT /api/admin/mcp/catalog/{server_id}/revoke`

*User API:*
- `GET /api/user/mcp/servers` — returns `{shared: [...], private: [...]}`
- `POST /api/user/mcp/servers` — add private server (gated by `web.allow_user_mcp` config, default `True`)
- `DELETE /api/user/mcp/servers/{server_id}` — remove private server

*Portal:*
- `UserSettings.jsx`: new `MCPServers` section — admin-granted list + private server list + add-private form (stdio/http)

*Audit:*
- 7 new `AuditAction` constants: `MCP_SERVER_CREATED/UPDATED/DELETED`, `MCP_ACCESS_GRANTED/REVOKED`, `USER_MCP_SERVER_ADDED/DELETED`

**Test suite:**
- `tests/test_mcp_catalog.py` — 65 tests: catalog CRUD, access control (7 scenarios), `UserMCPStore`, `connect_for_user` (6 scenarios incl. credential injection + clash), admin API (16 endpoints), user API (8 endpoints), audit constants
- **152 passed** across all Phase 1–3 test files (87 prev + 65 new)

---

### What's working and verified at commit `f76db1c`

**Phase 1 (portal SPA) — complete since `14b775a`:**
- User portal at `/app` — Dashboard (tasks + projects), Settings (profile, password, API keys)
- Role-based 302: non-admin authed users redirected from `/` to `/app/`
- `PUT /api/auth/me/password` — verified current pw, 8-char min

**Phase 2 — complete at `f76db1c`:**

*Credential vault (`orchid/vault/`):*
- `VaultStore` — Fernet-encrypted JSON at `~/.config/orchid/users/{uid}/credentials.json.enc`
- Key derivation: `HKDF-SHA256(ORCHID_VAULT_KEY, salt=b"orchid-vault-v1", info=user_id.encode())` → 32-byte Fernet key per user
- `GET /api/user/credentials` — list key names (no values)
- `PUT /api/user/credentials/{key}` — store/update secret
- `DELETE /api/user/credentials/{key}` — remove
- 503 with human-readable error if `ORCHID_VAULT_KEY` not set
- Portal `UserSettings.jsx`: `CredentialVault` section — lazy-load, list, add, delete, graceful 503 banner

*Per-user notification config:*
- `User.notification_config: dict` field — stored in `users.json` alongside other user data
- Keys: `email_enabled`, `email_address`, `telegram_enabled`, `telegram_chat_id`, `slack_enabled`, `slack_user_id`, `notify_on_success`, `notify_on_failure`
- `GET/PUT /api/user/config/notifications` — merge-on-PUT (doesn't wipe unspecified keys)
- `orchid/auth/notifications.py` — `dispatch_task_notification()` called by `CronEngine._run_task` after every run; email channel live, Telegram/Slack are logged stubs (Phase 3)
- `orchid/auth/mailer.py` — SMTP email via `SMTP_HOST/PORT/USER/PASSWORD/FROM/USE_SSL` env vars (same as `orchid-mcp-smtp`); graceful no-op if unconfigured
- Portal: `NotificationConfig` section — email/Telegram/Slack toggles with channel-specific inputs

*Admin-invite flow:*
- `InviteToken` dataclass — `token_id` (`inv_` + UUID hex), argon2-hashed secret, 48h TTL, `is_used` flag
- Stored in `users.json` under `"invites"` key; `FileUserStore` CRUD: `store_invite`, `get_invite`, `mark_invite_used`
- `POST /api/admin/invite` (admin-only) — creates inactive `User` + `InviteToken`, sends email (falls back gracefully if SMTP unconfigured), returns `{invite_url, email_sent, token_id, ...}`
- `GET /api/auth/invite/{token_id}` (public) — validates token, returns email; 404 if unknown/used, 410 if expired
- `POST /api/auth/invite/accept` (public) — verifies argon2 secret, activates user, sets password, issues JWT+refresh cookies; 401 wrong secret, 410 expired, 400 pw < 8 chars
- Portal `App.jsx`: `AcceptInvite` component — detects `?invite_id=&invite_token=` in URL before auth check; validates, shows email, password form, activates, cleans URL, reloads

**Test suite:**
- `tests/test_vault.py` — 23 tests: VaultStore unit (HKDF isolation, encryption at rest, wrong-key raises, delete_all) + vault API (list/set/delete, 503 on missing key, auth required) + notification config API (get/set/partial merge/unknown key)
- `tests/test_invite.py` — 20 tests: admin invite creation (duplicate email, invalid role, SMTP mock), token validation (expired, invalid), accept flow (activates user, issues cookie, marks used, reuse rejected, wrong secret, pw too short)
- **87 passed** across `test_web.py`, `test_web_v2.py`, `test_portal_api.py`, `test_vault.py`, `test_invite.py`

---

## 3. Decisions Made (and Why)

*(Decisions from Phase 1 unchanged — see `docs/multiuser-proposal.md` for the full resolved decisions table.)*

**Decision:** Vault key = separate `ORCHID_VAULT_KEY` env var, not derived from `JWT_SECRET`
**Reason:** JWT_SECRET rotation (e.g., after a breach) must not nuke all credential vaults. Independent env var = independent rotation. Both are required for a secure deployment.
**Reversibility:** Don't reopen. Architecture decision is in `vault/store.py` docstring.

**Decision:** Per-user vault key = `HKDF(ORCHID_VAULT_KEY, info=user_id)`, not `ORCHID_VAULT_KEY` directly
**Reason:** Each user gets a distinct Fernet key. Compromise of one user's derived key does not expose others. All keys still invalidated if `ORCHID_VAULT_KEY` rotates — this is documented, acceptable.
**Reversibility:** Could change derivation in a future version, but would require re-encrypting all vaults.

**Decision:** Admin-invite = email link (SMTP), graceful fallback to returning URL in API response
**Reason:** Internal tool — SMTP is often available. But if not configured, admin can copy the URL from the API response and paste it in Slack/email manually. Zero-config path works.
**Reversibility:** Easy to add other delivery methods later.

**Decision:** `notification_config` stored in `User` object (not a separate `config.yaml` file)
**Reason:** Simpler. `FileUserStore` already serializes all user fields to JSON. One fewer file per user. Notification config is small (8 keys). Proposal mentioned `config.yaml` but that's Phase 3+ scope for larger configs.
**Reversibility:** Could migrate to per-user `config.yaml` in Phase 3 without breaking existing data.

**Decision:** Telegram/Slack notification channels are stubs in Phase 2 (logged, not dispatched)
**Reason:** The existing Telegram/Slack bots use `orchid serve --telegram/--slack` which manages bot sessions centrally. Per-user DM routing requires wiring through those bots — that's Phase 3 scope (MCP catalog also lands there). The data model is complete; only the dispatch is stubbed.
**Reversibility:** Replace the TODO stubs in `orchid/auth/notifications.py`.

---

## 4. Architecture & Key Files

### Created in Phase 2

| File | What it does |
|------|-------------|
| `orchid/vault/__init__.py` | Module marker |
| `orchid/vault/store.py` | `VaultStore` — Fernet-encrypted per-user credential store. `get_vault()` singleton. `reset_vault()` for tests. |
| `orchid/vault/api.py` | `register_routes(app)` — installs `/api/user/credentials/*` and `/api/user/config/notifications` endpoints. Local imports only (no `from __future__ import annotations`). |
| `orchid/auth/mailer.py` | `send_invite()`, `send_task_notification()` — SMTP via env vars. `is_configured()` guard. Never raises. |
| `orchid/auth/notifications.py` | `dispatch_task_notification()` — reads `User.notification_config`, dispatches email (live) + Telegram/Slack (stubs). Called by `CronEngine._run_task`. |
| `tests/test_vault.py` | 23 tests for VaultStore + vault API + notification config API |
| `tests/test_invite.py` | 20 tests for admin invite creation + token validation + accept flow |

### Modified in Phase 2

| File | What changed |
|------|-------------|
| `orchid/auth/types.py` | Added `User.notification_config: dict`, `InviteToken` dataclass |
| `orchid/auth/base.py` | 3 new abstract methods: `store_invite`, `get_invite`, `mark_invite_used` |
| `orchid/auth/store.py` | `_invites: dict[str, InviteToken]` in `FileUserStore`; load/save; CRUD methods; `_parse_invite()` |
| `orchid/auth/audit.py` | 5 new `AuditAction` constants: `INVITE_SENT`, `INVITE_ACCEPTED`, `CREDENTIAL_UPDATED`, `CREDENTIAL_DELETED`, `NOTIFICATION_CONFIG_UPDATED` |
| `orchid/cron/engine.py` | `_run_task` calls `dispatch_task_notification()` after recording run (local import, never raises) |
| `orchid/interfaces/web_server.py` | Registers vault routes; adds `POST /api/admin/invite`, `GET /api/auth/invite/{id}`, `POST /api/auth/invite/accept` |
| `orchid/interfaces/portal/src/components/UserSettings.jsx` | Replaced Phase 2 stubs with `CredentialVault` + `NotificationConfig` components |
| `orchid/interfaces/portal/src/App.jsx` | `_parseInviteParams()` + `AcceptInvite` component; `AuthedApp` wrapper |
| `pyproject.toml` | `cryptography>=42.0.0` added as explicit dep (was transitive via authlib) |

### Do not touch without reason

- `orchid/web/server.py` — dead-end file, never loaded by `orchid serve`. Do not add routes here.
- `orchid/interfaces/web_ui/` — existing power-user SPA. Untouched since Phase 1.
- `orchid/auth/jwt.py` — crypto params settled.
- `orchid/interfaces/portal/vite.config.js` `base: '/app/'` — load-bearing, do not change.

---

## 5. Gotchas & Hard-Won Knowledge

*(All Phase 1 gotchas still apply — see below for new ones.)*

**`ORCHID_VAULT_KEY` must be set before any credential read/write.** `VaultStore._get_fernet()` raises `RuntimeError` if missing. `list_keys()` on an empty vault returns `[]` without needing the key (no file to decrypt). The API returns 503 with a human-readable error. Test: unset the var and `store.set()` raises.

**`issue_access_token` and `issue_refresh_token` both take a `User` object, not `user_id`.** Passing a string or dict raises `AttributeError`. See `orchid/auth/jwt.py` lines 53 and 77.

**`store.update_user()` not `store.upsert_user()`.** Already documented in Phase 1 — still true. `notification_config` updates use `update_user()`.

**Vault key derivation is HKDF, not HMAC.** `HKDF` from `cryptography.hazmat.primitives.kdf.hkdf`. Import path: `from cryptography.hazmat.primitives.kdf.hkdf import HKDF`. Do not use `hmac.new()` — HKDF has the right length expansion properties.

**`notification_config` keys use underscores, not hyphens.** `email_enabled`, `telegram_chat_id`, `slack_user_id` — all snake_case. The portal and backend agree on this; don't introduce camelCase.

**Invite token URL parameters are `invite_id` and `invite_token`**, not `token_id`/`secret`. `_parseInviteParams()` in `App.jsx` reads `invite_id` and `invite_token` from `window.location.search`. Backend endpoint at `GET /api/auth/invite/{token_id}` uses `token_id` in the path. Keep these straight.

**`CronEngine._run_task` notification dispatch is wrapped in bare `try/except`.** Notification failure must never crash the engine. The try/except in `notifications.py::dispatch_task_notification` also eats exceptions. This is intentional — logs are the signal.

**PyJWT and argon2-cffi still required in venv.** If auth endpoints 404: `python -c "from orchid.interfaces.web_server import _AUTH_AVAILABLE; print(_AUTH_AVAILABLE)"`. Fix: `uv pip install "PyJWT>=2.8.0" argon2-cffi cryptography`.

**Portal `dist/` not committed (gitignored).** Build before deployment: `cd orchid/interfaces/portal && npm install && npm run build`.

---

## 6. Conventions In Play

Same as Phase 1 — see that section. New addition:

**Vault API tests use `ORCHID_VAULT_KEY` in `os.environ`.** The `vault_client` fixture sets it and resets `orchid.vault.store._vault_instance` to a `VaultStore` pointing at `tmp_path/vaults`. Clean up via `reset_vault()` in teardown.

**`vault/api.py` uses `app.add_api_route(...)` pattern**, not `@app.get(...)` decorators. Same pattern as `cron/api.py`. Required because routes are defined inside `register_routes(app)`, not at module level.

---

## 7. Decisions Made in Phase 3

**Decision:** MCP catalog in separate `mcp_catalog.json` (not `users.json`)
**Reason:** System config vs. user identity data should not mix. Independent backup/export. Aligns with proposal spec.

**Decision:** `connect_for_user()` coexists with `connect()`
**Reason:** Zero breaking changes. CLI/project paths use `connect()`. User-scoped paths (cron executor) use `connect_for_user()`. Two clear entry points.

**Decision:** `allow_user_mcp` read from `orchid.config.get("web.allow_user_mcp", True)` at request time (not at route registration)
**Reason:** Allows live config toggle without restart. Default `True` (permissive for dev; operators can disable).

---

## 8. Open Questions for Phase 4

1. **Admin Console SPA build setup.** Phase 4 adds a second React root at `/admin`. Options: (a) new Vite project in `orchid/interfaces/admin/`; (b) same Vite project as portal with multiple entry points. Portal uses `base: '/app/'`. Admin would need `base: '/admin/'`. Separate project = clear isolation, easy to gate. **Recommend: separate Vite project.**

2. **Telegram/Slack notification wiring (still deferred).** Stubs in `orchid/auth/notifications.py`. Wire through `CentralBotManager` in `orchid/interfaces/central_bot.py` — use lazy local import. Phase 4 can do this as a side task.

3. **`readonly` role in portal.** Currently same view as `user`. Does Phase 4 add a stripped-down view? Or is `readonly` only for admin console ACL? Decision not required until Admin Console SPA lands.

---

## 9. Do Not Touch

- **`orchid/web/server.py`** — dead-end, never loaded by `orchid serve`.
- **`orchid/interfaces/web_ui/`** — existing power-user SPA.
- **`orchid/auth/jwt.py`** — crypto params settled.
- **`orchid/interfaces/portal/vite.config.js` `base: '/app/'`** — load-bearing.
- **`.claude/settings.local.json`** — local harness config, never commit.
- **`docs/multiuser-proposal.md` decisions section** — resolved. Don't re-debate.

---

## 10. Resume Command

> Read `HANDOFF.md`. We're building multi-user support for Orchid (internal agentic OS). Phases 1, 2, and 3 are complete at commit `3e76d22` — 152 tests pass. Start Phase 4: Admin Console SPA at `/admin`. Spec is in `docs/multiuser-proposal.md` under "Phase 4". Before writing code, ask: (1) Separate Vite project for the admin SPA, or multi-entry in the portal project? (2) Which admin pages to build first — Users, MCP Catalog, Audit Log, or all at once? Do not touch `orchid/web/server.py` (dead-end file). Do not change portal `vite.config.js` `base: '/app/'`. Commit between major changes.
