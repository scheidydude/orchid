# Multi-User Support: Current State Review

**Date:** 2026-05-26  
**Branch:** main (V2.5)  
**Reviewer:** Claude Sonnet 4.6

---

## 1. What Exists Today

### Auth Foundation (solid)
| Capability | Status |
|---|---|
| JWT HS256 + refresh tokens (HttpOnly cookies) | ✅ Complete |
| Argon2id password hashing | ✅ Complete |
| API keys (`ok_` prefix, scoped) | ✅ Complete |
| OIDC SSO (Google, Entra, generic) | ✅ Complete |
| PKCE mobile flow | ✅ Complete |
| Append-only audit log (daily JSONL) | ✅ Complete |
| Role model: `user` / `admin` / `readonly` | ✅ Partial |
| Per-user project scoping (`User.projects`) | ✅ Partial |
| Budget fields (`budget_usd`, `cpu_budget_seconds`) | ✅ Fields exist, unenforced |

### Scheduled Tasks
- `ScheduledTask.owner_id` exists — tasks are user-scoped by design
- `CronEngine` / APScheduler runs tasks globally; no per-user isolation at exec layer
- `TaskRunStore` is a single global JSONL — no per-user query isolation beyond `owner_id` filter
- `executor.py` resolves MCP servers from **global** config, not per-user

### Projects
- `ProjectRegistry` is global (single `projects.json`)
- `User.projects` list gates which project IDs a user may access
- No per-user project creation, config, or credential namespace

### MCP
- `MCPManager` reads from `mcp_servers` top-level config key
- No per-user MCP server list
- No per-user credential injection (env vars) at MCP level
- No admin-controlled server allow/deny lists per role or user

---

## 2. Gaps for True Multi-User Agentic OS

### 2a. User Identity / Namespace
- No per-user config directory (`~/.config/orchid/users/{user_id}/`)
- No per-user credential store (API keys for LLM providers, 3rd-party tokens)
- No per-user `.env` injection into agent/MCP execution context
- All users share a single `users.json` — fine for small teams; single point of failure at scale

### 2b. MCP Server Access Control
- No admin-defined MCP server catalog (shared vs private vs forbidden)
- Users cannot add personal MCP servers without touching global config
- No per-server scope tagging (e.g., `scope:filesystem` requires admin grant)
- No isolation of MCP credentials between users (if two users share a server, they share its env/auth context)

### 2c. Agent Execution Isolation
- `BackgroundRunner` / `AgentManager` is global — no per-user job queue or resource cap enforcement
- `budget_usd` and `cpu_budget_seconds` fields exist on `User` but no enforcement path
- Provider registry (`orchid/providers/`) reads from project or global config — no per-user API key resolution
- Parallel agents from different users contend on same `asyncio` semaphore (`D0022`) with no user-level fairness

### 2d. UI / UX
- Current web UI is single-page, project-centric — assumes power user / admin persona
- No user-facing landing page / dashboard (scheduled tasks + active projects)
- No user settings page (profile, credentials, notification prefs)
- No visual distinction between admin views and user views
- Admin functions (user management, audit log, system config) mixed with operational views

### 2e. Configuration
- 3-layer config (D0005) covers CLI > project > env, not per-user
- No wizard or UI flow for a new user to configure their LLM provider key
- MCP server wizard (D0047) is admin-level

---

## 3. What Claude Cowork Does That Orchid Doesn't (Yet)

| Feature | Claude Cowork | Orchid |
|---|---|---|
| Per-user credential vault | ✅ | ❌ |
| Per-user conversation / task history | ✅ | Partial (owner_id) |
| User-facing dashboard (tasks, projects) | ✅ | ❌ |
| Admin grants tool/server access per user or team | ✅ | ❌ |
| Shared + private MCP servers | ✅ | ❌ |
| User self-service credential setup | ✅ | ❌ |
| Admin console separate from user console | ✅ | ❌ |
| Resource quotas enforced at runtime | ✅ | Partial (fields) |

---

## 4. Risk / Technical Debt

| Item | Severity |
|---|---|
| `ProjectRegistry` global — no per-user namespace | High |
| `MCPManager` global config — shared credentials leak between users | High |
| Budget fields unenforced — can't bill or cap users | Medium |
| Single `users.json` — no migration path to Postgres for >50 users | Medium |
| Admin routes unprotected by feature flags — any admin can do anything | Low |
| `ScheduledTask` config stores plaintext credentials in `config` dict | Medium |
