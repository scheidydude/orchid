# Auth & Multi-User Support Plan

**Status:** ✅ Complete (V2.3)
**Implemented:** 2026-05-09
**Scope:** Web UI, REST API, mobile clients, Telegram/Slack interfaces

---

## Current State

### What Exists

`orchid/auth/` module with four files:

| File | Purpose |
|------|---------|
| `types.py` | `User`, `AuthToken` dataclasses |
| `store.py` | JSON-backed `UserStore` (`~/.config/orchid/users.json`) |
| `middleware.py` | FastAPI Bearer dependencies: `get_current_user`, `get_optional_user`, `require_auth(role=)` |
| `__init__.py` | Empty |

Web endpoints in `orchid/web/server.py`:
- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/token`
- `GET  /api/auth/me`
- `POST /api/auth/logout`
- `GET  /api/auth/users` (admin only)

Roles on `User`: `user`, `admin`, `readonly`

Telegram: integer whitelist via `TELEGRAM_ALLOWED_USERS` env var (D0015).

### Critical Bugs (Must Fix Before Anything Else)

| Bug | Location | Impact |
|-----|----------|--------|
| Passwords accepted but **never stored or verified** — login only checks username exists | `server.py:127-139` | Anyone can log in as any user |
| Tokens stored **in-memory only** (`_auth_tokens` dict) — wiped on restart | `server.py:34` | All sessions invalidated on every restart |
| `UserStore.list_users()` called in middleware but **not defined** in store | `middleware.py:42`, `store.py` | Runtime crash on any auth check |
| No password hashing — plaintext comparison if a hash were stored | `server.py` | Credential exposure if DB leaks |

### Missing Features

- No OAuth 2.0 / OIDC (Google, Microsoft Entra ID, GitHub, etc.)
- No JWT — stateless tokens that survive restart
- No refresh tokens
- No PKCE flow for mobile clients (required for secure mobile OAuth)
- No per-user project scoping enforcement (field exists, not enforced)
- No API key auth for programmatic/CI access
- No session revocation list for JWT
- No audit log

---

## Target Architecture

```
orchid/auth/
  __init__.py
  types.py            ← User, AuthToken, OAuthAccount, ApiKey, AuditEvent
  store.py            ← UserStore with password hash, list_users, oauth linking
  jwt.py              ← issue/verify JWTs, refresh tokens, revocation
  middleware.py       ← FastAPI deps using JWT; fallback to API key
  providers/
    __init__.py
    base.py           ← OIDCProvider ABC
    google.py         ← Google OIDC
    entra.py          ← Microsoft Entra ID (Azure AD) OIDC
    oidc_generic.py   ← any standards-compliant OIDC provider
```

**Token flow (web + mobile):**

```
Client → POST /api/auth/login (username+password)
       ← access_token (JWT, 15 min) + refresh_token (opaque, 30 days)

Client → GET /api/* with Bearer <access_token>
       ← resource (no DB hit — JWT is self-contained)

Client → POST /api/auth/refresh with refresh_token
       ← new access_token + new refresh_token (rotation)

Client → POST /api/auth/logout
       ← revokes refresh_token, adds jti to revocation list
```

**OAuth flow (web):**

```
Client → GET /api/auth/oauth/<provider>/start
       ← redirect to provider authorization URL

Provider → GET /api/auth/oauth/<provider>/callback?code=...&state=...
         → validate state, exchange code, fetch userinfo
         → create/link User in store
         ← set access_token + refresh_token (same JWT flow)
```

**OAuth flow (mobile — PKCE):**

```
Mobile app generates code_verifier + code_challenge
Mobile → GET /api/auth/oauth/<provider>/start?code_challenge=...&method=S256
       ← redirect to provider (no client secret leaves the server)

Provider → deep link back to app with code
Mobile → POST /api/auth/oauth/<provider>/token {code, code_verifier}
        ← access_token + refresh_token
```

---

## Dependencies

| Library | Purpose | Replaces |
|---------|---------|---------|
| `argon2-cffi` | Password hashing (Argon2id) | plaintext |
| `python-jose[cryptography]` | JWT issue/verify (RS256 or HS256) | in-memory dict |
| `authlib` | OAuth2/OIDC client (Google, Entra, any OIDC) | nothing |
| `httpx` | Async HTTP for OIDC token exchange | — |

Add to `pyproject.toml` under `[project.dependencies]`.

---

## Provider Configuration

In `~/.config/orchid/config.yaml` (or `.env` for secrets):

```yaml
auth:
  jwt_algorithm: HS256          # or RS256 with key files
  jwt_secret: "${JWT_SECRET}"   # env var
  access_token_ttl_minutes: 15
  refresh_token_ttl_days: 30

  providers:
    - type: google
      client_id: "${GOOGLE_CLIENT_ID}"
      client_secret: "${GOOGLE_CLIENT_SECRET}"
      redirect_uri: "https://your-host/api/auth/oauth/google/callback"

    - type: entra
      tenant_id: "${AZURE_TENANT_ID}"
      client_id: "${AZURE_CLIENT_ID}"
      client_secret: "${AZURE_CLIENT_SECRET}"
      redirect_uri: "https://your-host/api/auth/oauth/entra/callback"

    # Any OIDC-compliant provider
    - type: oidc
      name: "company-sso"
      discovery_url: "https://sso.company.com/.well-known/openid-configuration"
      client_id: "${SSO_CLIENT_ID}"
      client_secret: "${SSO_CLIENT_SECRET}"
      redirect_uri: "https://your-host/api/auth/oauth/company-sso/callback"
```

Env vars for secrets; config file for non-sensitive settings. Follows D0030 resolution order.

---

## Phases

### Phase 1 — Fix the Broken Foundation

**Goal:** Auth that actually works. No OAuth yet. Prerequisite for all other phases.

**Tasks:**

1. Add `list_users()` to `UserStore` (`store.py:85`) — fixes middleware crash
2. Add `argon2-cffi` — hash password on register, verify on login
3. Add `jwt.py` — issue/verify JWTs, replace in-memory `_auth_tokens` dict
4. Add refresh token endpoint: `POST /api/auth/refresh`
5. Update `middleware.py` to validate JWT instead of scanning token dict
6. Store refresh tokens in `UserStore` (persisted JSON) with expiry
7. Add `POST /api/auth/logout` to revoke refresh token (access token expires naturally)
8. Write tests covering: register → login → call API → refresh → logout

**Deliverable:** Stateless JWT auth that survives restart, passwords properly hashed, no runtime crashes.

**Estimated effort:** 1–2 days

---

### Phase 2 — API Keys for Programmatic Access

**Goal:** CI/CD, scripts, and bots can authenticate without user sessions.

**Tasks:**

1. Add `ApiKey` dataclass to `types.py`: `{key_id, secret_hash, user_id, name, scopes, created_at, last_used, expires_at}`
2. Add API key CRUD to `UserStore`
3. Add endpoints:
   - `POST /api/auth/apikeys` — create key (returns secret once, then gone)
   - `GET  /api/auth/apikeys` — list user's keys (no secrets)
   - `DELETE /api/auth/apikeys/{key_id}` — revoke
4. Update `middleware.py` to check `Authorization: Bearer` for either JWT or API key prefix (`ok_...`)
5. Scope enforcement: keys carry a list of allowed actions (e.g. `["tasks:run", "tasks:read"]`)

**Deliverable:** Non-interactive auth for mobile background sync, CLI scripts, CI pipelines.

**Estimated effort:** 1 day

---

### Phase 3 — OAuth 2.0 / OIDC Providers

**Goal:** Google and Microsoft Entra ID SSO. No more manual user registration for org members.

**Tasks:**

1. Add `OAuthAccount` dataclass: `{provider, provider_user_id, user_id, email, access_token, refresh_token, expires_at}`
2. Add `providers/base.py` — `OIDCProvider` ABC: `start_url()`, `handle_callback()`, `refresh()`
3. Implement `providers/google.py` using `authlib`
4. Implement `providers/entra.py` using `authlib` (tenant-aware)
5. Implement `providers/oidc_generic.py` — discovery-URL-based, covers any OIDC provider
6. Add provider registry: loads from config, maps slug → provider instance
7. Add endpoints:
   - `GET /api/auth/oauth/{provider}/start` — redirect to provider
   - `GET /api/auth/oauth/{provider}/callback` — exchange code, issue Orchid JWT
8. Account linking: if email already exists in `UserStore`, link OAuth account to it
9. Update `UserStore` to persist `OAuthAccount` records
10. Write tests with mocked OIDC responses

**Deliverable:** "Sign in with Google" and "Sign in with Microsoft" on the web UI.

**Estimated effort:** 2–3 days

---

### Phase 4 — Mobile Client Support (PKCE)

**Goal:** iOS/Android apps can initiate and monitor tasks without storing client secrets.

**Tasks:**

1. Add PKCE support to OAuth start/callback endpoints:
   - Accept `code_challenge` + `code_challenge_method=S256` on `/start`
   - Verify `code_verifier` on token exchange
2. Add `POST /api/auth/oauth/{provider}/token` — mobile-only token exchange endpoint
3. Add deep-link redirect URI support (e.g. `orchid://auth/callback`)
4. Add `POST /api/tasks/{id}/run` endpoint gated by auth (mobile initiates tasks)
5. Add `GET /api/tasks/{id}/stream` — SSE stream of task output (replaces WebSocket for mobile compatibility)
6. Ensure all task-mutation endpoints enforce `tasks:run` scope
7. Document mobile auth flow for app developers

**Deliverable:** Mobile app can OAuth-authenticate, start tasks, and stream output without secrets on device.

**Estimated effort:** 2 days

---

### Phase 5 — Audit Log & Per-User Project Scoping

**Goal:** Enterprise-grade accountability and access control.

**Tasks:**

1. Add `AuditEvent` dataclass: `{event_id, user_id, action, resource, result, timestamp, ip}`
2. Add `AuditStore` (append-only JSON log, rotated daily)
3. Hook audit writes into: login, logout, token issue, task run, project access
4. Enforce `User.projects` list — reject task runs on projects not in user's allowed list
5. Add `GET /api/audit` endpoint (admin only, paginated)
6. Add user management endpoints (admin):
   - `PUT /api/auth/users/{id}` — update role, projects, active status
   - `DELETE /api/auth/users/{id}` — deactivate

**Deliverable:** Full audit trail; admins control which users can touch which projects.

**Estimated effort:** 1–2 days

---

## Endpoint Summary (Post All Phases)

```
POST   /api/auth/register                    Phase 1 (fixed)
POST   /api/auth/login                       Phase 1 (fixed)
POST   /api/auth/refresh                     Phase 1
POST   /api/auth/logout                      Phase 1 (fixed)
GET    /api/auth/me                          Phase 1 (fixed)

POST   /api/auth/apikeys                     Phase 2
GET    /api/auth/apikeys                     Phase 2
DELETE /api/auth/apikeys/{key_id}            Phase 2

GET    /api/auth/oauth/{provider}/start      Phase 3 (+PKCE Phase 4)
GET    /api/auth/oauth/{provider}/callback   Phase 3
POST   /api/auth/oauth/{provider}/token      Phase 4 (mobile)

GET    /api/auth/users                       Phase 5 (admin)
PUT    /api/auth/users/{id}                  Phase 5 (admin)
DELETE /api/auth/users/{id}                  Phase 5 (admin)
GET    /api/audit                            Phase 5 (admin)
```

---

## Interface-Specific Notes

### Telegram (D0014, D0015)
- Current whitelist (`TELEGRAM_ALLOWED_USERS`) stays for backward compat
- Phase 3+: add `/link` command — user DMs bot a one-time code generated by web UI to link Telegram ID to Orchid account
- Linked users inherit Orchid role and project scoping

### Slack (D0024, D0025)
- Same linking approach as Telegram via slash command
- Slack user ID stored on `User` record

### Web UI
- Phase 1: add login form, store JWT in `localStorage`, attach as `Authorization` header
- Phase 3: add "Sign in with Google / Microsoft" buttons on login page

---

## Security Notes

- JWT secret via env var only — never in config file committed to git
- Argon2id parameters: `time_cost=3`, `memory_cost=65536`, `parallelism=4` (OWASP recommended)
- Access tokens short-lived (15 min) — limits blast radius of token leak
- Refresh token rotation — old token invalidated on each use
- PKCE required for all mobile flows — no client secrets on device
- State parameter required on all OAuth initiations — prevents CSRF
- Audit log append-only — no delete endpoint, even for admin
