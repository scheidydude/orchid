# Multi-User Agentic OS: Architecture Proposal

**Date:** 2026-05-26  
**Status:** Draft for review  
**Scope:** Orchid V3 — Internal multi-user agentic OS

---

## Decision: One Site or Two?

**Recommendation: One unified FastAPI app, two React SPA roots, role-based routing.**

| Option | Pros | Cons |
|---|---|---|
| **A) Single app, two React roots** (recommended) | One deploy, one auth session, one TLS cert. Admin can "view as user". Shared API layer. | Slightly more complex build setup. |
| B) Separate admin site (different port/subdomain) | Clean separation | Two deploys, two auth flows, CORS complexity, admin must re-login |
| C) Single SPA with role-based tabs | Simplest | Admin UI bleeds into user view; hard to lock down per role |

**Why A:** Admin logs in once and can switch between `[Admin Console]` and `[User Portal]` views in the nav. Different React roots (`/admin/*` and `/app/*`) served by the same FastAPI, same `orchid serve` command. Admin visiting `/app/*` sees exactly what users see — useful for debugging. Non-admin users get redirected away from `/admin/*`.

---

## Architecture Overview

```
orchid serve --port 7842
│
├── FastAPI (single process)
│   ├── /api/auth/*           ← shared auth (JWT, OIDC, API keys)
│   ├── /api/admin/*          ← admin API (users, system config, MCP catalog, audit)
│   ├── /api/user/*           ← user API (dashboard, tasks, projects, credentials)
│   ├── /api/projects/*       ← existing project API (stays, gains user scoping)
│   ├── /api/scheduler/*      ← existing cron API (stays)
│   ├── /app/*                ← User Portal SPA (React)
│   ├── /admin/*              ← Admin Console SPA (React)
│   └── /                     ← redirect: admin→/admin, user→/app
│
└── Static files
    ├── static/app/           ← User Portal build
    └── static/admin/         ← Admin Console build
```

---

## User Portal (`/app`)

### Landing Dashboard (default after login)
```
┌─────────────────────────────────────────────────────┐
│  🌸 Orchid   [My Tasks] [Projects] [Settings]  [You▾]│
├─────────────────────────────────────────────────────┤
│  Scheduled Tasks                     [+ New Task]   │
│  ┌─────────────────────────────────────────────┐    │
│  │ ● Daily standup report   next: 9:00am   ▶ ⋯ │    │
│  │ ✓ Weekly digest          ran: 2h ago    ▶ ⋯ │    │
│  │ ✗ Slack summary          failed: 1d ago ▶ ⋯ │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  My Projects                         [+ New]        │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │ project-alpha│  │ project-beta │                 │
│  │ 3 tasks      │  │ idle         │                 │
│  └──────────────┘  └──────────────┘                 │
└─────────────────────────────────────────────────────┘
```

### Pages
- **Dashboard** — scheduled tasks (status, next run, last run) + projects
- **Task Detail** — run history, logs, edit schedule/config
- **Project** — tasks.md viewer/editor, run controls, live log stream
- **Settings** — profile, credential vault, notification prefs, API keys
- **Credentials** — per-user LLM provider keys, 3rd-party tokens (encrypted at rest)

---

## Admin Console (`/admin`)

### Pages
- **Users** — list, invite, deactivate; set role, projects, budget
- **MCP Catalog** — define shared servers; tag scope; grant/deny per role or user
- **System Config** — global defaults, provider fallbacks, feature flags
- **Audit Log** — paginated, filterable by user / action / date
- **Quotas** — set and monitor `budget_usd` / `cpu_budget_seconds` per user
- **Task Monitor** — all users' running and recent tasks

Admin accessing `/app` sees the user portal as themselves — no special "admin mode" there. Admin-specific actions are always in `/admin`.

---

## New Backend Components

### 1. Per-User Credential Vault

```
~/.config/orchid/users/{user_id}/
    credentials.json.enc   ← encrypted (Fernet, key derived from JWT_SECRET + user_id)
    mcp_servers.yaml       ← user's private MCP server definitions
    config.yaml            ← user overrides (provider, model preferences)
```

**API:**
```
GET  /api/user/credentials          ← list credential keys (no values)
PUT  /api/user/credentials/{key}    ← store encrypted value
DELETE /api/user/credentials/{key}
GET  /api/user/config               ← user's config overrides
PUT  /api/user/config
```

Credential values injected into agent/MCP execution context at dispatch time — never logged, never stored in `ScheduledTask.config`.

### 2. MCP Server Catalog (Admin-Managed)

New data model alongside `UserStore`:

```python
@dataclass
class MCPServerEntry:
    server_id: str          # e.g. "gmail", "filesystem", "custom-crm"
    name: str
    transport: str          # stdio | http
    config: dict            # command/url, args — no user secrets here
    scope: str              # "shared" | "private" | "admin-only"
    allowed_roles: list     # ["user","admin"] or ["admin"]
    allowed_users: list     # specific user_ids override; empty = role-based
    requires_credential: str | None  # credential key user must supply
```

**Admin API:**
```
GET  /api/admin/mcp/catalog
POST /api/admin/mcp/catalog
PUT  /api/admin/mcp/catalog/{server_id}
DELETE /api/admin/mcp/catalog/{server_id}
PUT  /api/admin/mcp/catalog/{server_id}/grant   ← grant user or role access
```

**User API:**
```
GET  /api/user/mcp/servers          ← servers admin granted to this user
POST /api/user/mcp/servers          ← add private server (if admin permits)
```

`MCPManager` updated: `connect_for_user(user_id)` merges shared catalog servers the user can access + user's private servers, injecting per-user credentials from vault.

### 3. Provider Resolution — Per-User

Extend D0030 resolution order:

```
CLI > user credentials vault > project providers > global providers > env > defaults
```

`TaskExecutor.execute(task_dict, owner_id)` — already takes `owner_id`. Load user's vault, inject into provider registry context. No change to public API.

### 4. Budget Enforcement

`BudgetGuard` middleware:
- On every LLM call: check `User.budget_usd` remaining
- Increment cost counter in `UserStore` after call completes
- 429 response with `Retry-After` if over budget
- Admin API to reset or top up budget
- Daily `cpu_budget_seconds` enforced in `BackgroundRunner` per active user

### 5. Project Namespace

`ProjectRegistry` gains user context:
- `registry.list_projects(user_id=...)` — filters by `User.projects`
- `registry.create_project(user_id=..., ...)` — creates under user namespace
- Project paths: `~/.config/orchid/projects/{user_id}/{project_slug}/` for user-owned projects; existing shared paths unchanged

---

## Migration Plan

### Phase 1 — User Portal SPA (no backend changes)
- New React app at `/app` with dashboard, task list, project list
- Reads existing `/api/scheduler/tasks` and `/api/projects` (already owner-scoped)
- Login → redirect based on role (`admin` → `/admin`, else → `/app`)

### Phase 2 — Credential Vault + User Config + Notifications
- `~/.config/orchid/users/{user_id}/` namespace
- Encrypted credentials store (Fernet)
- Per-user notification config: email, Telegram chat ID, Slack DM
- User settings page in portal (profile, credentials, notification prefs)
- Admin-invite flow: admin creates user, user sets password on first login

### Phase 3 — MCP Catalog + Per-User Server Access
- `MCPServerEntry` catalog data model + admin API
- `MCPManager.connect_for_user()` — merges shared + private + credentials
- User portal: "My MCP Servers" view

### Phase 4 — Admin Console SPA
- React app at `/admin`
- Users page, MCP catalog, audit log, quota monitor
- Admin role enforcement at API layer (already exists, surface in UI)

### Phase 5 — Budget Enforcement + Per-User Provider Resolution
- `BudgetGuard` on LLM calls
- Provider resolution extended to user vault
- `cpu_budget_seconds` enforcement in runner

---

## Config (`orchid.yaml`) Additions

```yaml
web:
  user_portal: true          # enable /app SPA
  admin_console: true        # enable /admin SPA
  allow_user_mcp: true       # users may add private MCP servers
  allow_user_projects: true  # users may create projects

multi_user:
  credential_encryption: fernet   # fernet | none (dev only)
  default_budget_usd: 10.0        # 0 = unlimited
  default_cpu_seconds: 0          # 0 = unlimited
  mcp_catalog_path: ~/.config/orchid/mcp_catalog.json
```

---

## Security Considerations

| Concern | Mitigation |
|---|---|
| User credentials leaked via logs | Vault values never appear in audit log; injected at exec time only |
| User A accesses User B's tasks | `owner_id` check on all scheduler endpoints (already implemented) |
| User adds malicious MCP server | `allow_user_mcp` flag; admin can disable; all stdio commands logged |
| Shared MCP server leaks data between users | Per-connection isolation: `connect_for_user()` spins fresh adapter per execution |
| Admin console exposed to non-admin | `require_auth(role="admin")` on all `/api/admin/*` routes; SPA redirect on load |
| Credential vault key compromise | Fernet key = HMAC(JWT_SECRET, user_id) — rotating `JWT_SECRET` invalidates all vaults; document this |

---

## Open Questions

| Question | Decision |
|---|---|
| Postgres migration | Phase 5 |
| Team / group model | Admin-controlled `User.projects` list only — no team entity |
| MCP credential sharing | No — credentials are per-user, never shared |
| Notification delivery | Email + Telegram + Slack per-user notification in Phase 2 |
| User self-registration | Admin-invite only — no open registration |

*Resolved 2026-05-26.*

---

## Effort Estimate

| Phase | Scope | Estimate |
|---|---|---|
| Phase 1 — User Portal SPA | New React app, role-based routing | 3–5 days |
| Phase 2 — Credential Vault | Backend + settings UI | 2–3 days |
| Phase 3 — MCP Catalog | Data model + admin/user APIs + MCPManager update | 3–4 days |
| Phase 4 — Admin Console SPA | New React app, CRUD pages | 3–5 days |
| Phase 5 — Budget + Providers | Enforcement layer | 2–3 days |
| **Total** | | **13–20 days** |

All phases are additive — no breaking changes to existing CLI or project-based workflows.
