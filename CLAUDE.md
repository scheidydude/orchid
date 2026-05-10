<!-- compressed 2026-05-09 -->

# CLAUDE.md — Orchid Framework (v2.3)

## Core
Standalone AI agent orchestration. Tool (`~/orchid/`) invokes external projects (`~/projects/<name>/`). Projects opt-in via `CLAUDE.md` + `tasks.md` + `.orchid.yaml`.

## Layout
`~/projects/<name>/.orchid/`: `decisions.json`, `session_logs/`, `chroma/`, `task_results.json`.

## CLI
`orchid --project <path> --mode auto|interactive [--code-model] [--provider] [--offline]`
`orchid init <path>`, `orchid decide "Title" --decision "..."`, `orchid new "<desc>"`.
`orchid serve [--watch-dir] [--port 7842] [--telegram|--slack|--bots]` (Unified entry).
`orchid --status|--recall "q"|--search "q"|--add-task "t"|--run-task T001|--approve`.
`orchid --check-providers`.
*Deprecated:* `orchid telegram|slack|web` → use `orchid serve --telegram/--slack`.

## Tasks (`tasks.md`)
`- [ ] **T001** Title \`type:code_generate\` \`p1\` \`needs:T002\` \`model:claude\``.
Skip: `- [~] **T003**`. Rollup: `- [ ] **T099** \`type:rollup\` \`rollup:T090,T091\` \`output:FILE.md\``.

## Tool Calls (ReAct)
`Action: <name>\nAction Input: <json>`. Actions: `read_file`, `list_dir`, `bash`, `write_file` (replace), `append_file` (add), `delegate`.

## Architecture Decisions
**D0001** File-state. **D0002** 2-tier routing (Claude/llama). **D0003** ReAct text. **D0004** Interface-agnostic. **D0005** 3-layer config. **D0006** Standalone runtime. **D0007** Embed Chroma. **D0008** Embed: llama→ST. **D0009** Auto-embed/recall. **D0010** Search: SearXNG→Brave. **D0011** Extract: trafilatura. **D0012** Delegate depth 3. **D0013** Sub-context. **D0014** Telegram logic. **D0015** User whitelist. **D0016** Model routing. **D0017** Task deps. **D0018** Live log. **D0019** Inject queue. **D0020** Telegram notify. **D0021** Process parallelism. **D0022** Claude sem. **D0024** Slack Socket. **D0025** Slack threads. **D0026** Shared Runner. **D0027** Web FastAPI/React. **D0028** React dist. **D0029** Traefik TLS. **D0030** ProviderBase ABC; resolution order: CLI > project providers.<agent> > project providers.task_types.<type> > task annotation > env > type/agent defaults. **D0031** Shared backends. **D0032** Provider check. **D0033** Watchdog. **D0034** Orchid serve. **D0035** AgentManager. **D0036** XDG config. **D0037** Rollup Claude. **D0038** TaskResultStore. **D0039** Shell allowlist. **D0040** Tiktoken chunking. **D0041** V2 Lifecycle. **D0042** Strategic agents. **D0043** Gates. **D0044** Machine profile. **D0045** Web Planning. **D0046** WS Stream. **D0047** Wizard. **D0048** Prompt cache. **D0049** KV cache. **D0050** CentralBot. **D0051** Telegram state. **D0052** Slack map. **D0053** Bot serve. **D0054** JWT auth: HS256 access tokens (15 min) + opaque refresh tokens (30 days, argon2-hashed, rotated on use); HttpOnly cookies for web, Bearer header for API/mobile. **D0055** Argon2id passwords: time=3, mem=64MB, par=4 (OWASP); no plaintext ever stored. **D0056** API keys: `ok_{key_id}.{secret}` format; argon2-hashed secret, O(1) lookup; scopes list; `require_scope()` FastAPI dependency; JWT sessions unrestricted. **D0057** OIDC provider registry: `GenericOIDCProvider` fetches discovery doc, caches metadata; `GoogleOIDCProvider` + `EntraOIDCProvider` subclass; `ProviderRegistry.from_config()` loads from `auth.providers` YAML; account linking by email. **D0058** PKCE S256 mobile flow: `code_challenge` stored in OAuth state; server-side `_verify_pkce_s256()` (timing-safe) before provider exchange; `POST /api/auth/oauth/{p}/token` returns JSON tokens (no cookies); `code_verifier` forwarded to provider. **D0059** Audit log: append-only JSONL, daily rotation (`audit-YYYY-MM-DD.jsonl`), archived forever, thread-safe; 12 action constants; fire-and-forget `_log_audit()` never raises. **D0060** Per-user project scoping: `User.projects` list; empty = unrestricted; admin always bypasses; `_check_project_access()` raises 403; admin endpoints `PUT/DELETE /api/auth/users/{id}`.

## Auth Module (`orchid/auth/`)
`types.py` — `User`, `RefreshToken`, `ApiKey`, `OAuthAccount`, `AuditEvent` dataclasses.
`store.py` — `UserStore`: thread-safe JSON-backed; users, refresh tokens, API keys, OAuth accounts.
`jwt.py` — `hash_password`, `verify_password`, `issue_access_token`, `verify_access_token`, `issue_refresh_token`, `verify_refresh_token`, `issue_api_key`, `verify_api_key`.
`middleware.py` — `get_current_user`, `get_optional_user`, `require_auth(role=)`, `require_scope(scope=)`.
`audit.py` — `AuditStore` (JSONL, daily rotation), `AuditAction` constants, `make_event()`.
`providers/` — `OIDCProvider` ABC, `GenericOIDCProvider`, `GoogleOIDCProvider`, `EntraOIDCProvider`, `ProviderRegistry`.

## Auth Endpoints
`POST /api/auth/register` — hash password, store user.
`POST /api/auth/login` — verify hash, issue JWT + refresh, set HttpOnly cookies.
`POST /api/auth/refresh` — rotate refresh token, issue new pair.
`POST /api/auth/logout` — revoke refresh token, clear cookies.
`GET  /api/auth/me` — current user info.
`POST /api/auth/token` — verify JWT, return user_id.
`GET  /api/auth/users` — list users (admin).
`PUT  /api/auth/users/{id}` — update role/projects/is_active/email (admin).
`DELETE /api/auth/users/{id}` — deactivate + revoke sessions (admin, preserves record).
`POST /api/auth/apikeys` — create API key (secret shown once).
`GET  /api/auth/apikeys` — list keys (no secrets).
`DELETE /api/auth/apikeys/{id}` — revoke key.
`GET  /api/auth/oauth/providers` — list configured SSO providers.
`GET  /api/auth/oauth/{p}/start[?code_challenge=]` — redirect to provider (PKCE optional).
`GET  /api/auth/oauth/{p}/callback` — web callback: set cookies + redirect.
`POST /api/auth/oauth/{p}/callback` — POST callback (some providers).
`POST /api/auth/oauth/{p}/token` — mobile PKCE exchange: `{code, state, code_verifier}` → JSON tokens.
`GET  /api/audit` — paginated audit log (admin, filter by user_id/action).

## Required Env Vars (Auth)
`JWT_SECRET` — required; never commit. `GOOGLE_CLIENT_ID/SECRET`, `AZURE_TENANT_ID/CLIENT_ID/SECRET` — optional OAuth.

## Current State
**V2.3 Complete. 1500+ tests passing (134 auth + 68 Phase 1–6 reliability tests).**
*   **T051** Shell allowlist + BPE chunking.
*   **T053** V2 lifecycle + strategic agents.
*   **T054/55** Web UI Planning tab + Discussion streaming.
*   **T056** Prompt caching (D0048).
*   **T058–T059** Code review anthropic.py.
*   **T060** File Writing Guidelines.
*   **T061** CentralBotManager.
*   **T064** Fix --log-level.
*   **T066** README V2.1 docs.
*   **T068** systemd service.
*   **T077/78** Docs/README updated.
*   **T086** PM Guide (`docs/pm-guide.md`): Workflow, Wizard, Phases, Dashboard, Mobile monitoring, Glossary.
*   **Auth P1** JWT foundation: argon2id passwords, HS256 JWT, refresh tokens, HttpOnly cookies (D0054–D0055).
*   **Auth P2** API keys: `ok_` prefix, argon2 hash, scopes, `require_scope()` dependency (D0056).
*   **Auth P3** OIDC: Google, Entra, generic discovery, account linking, `ProviderRegistry` (D0057).
*   **Auth P4** PKCE mobile: S256 verify, `/token` endpoint, code_verifier forwarded to provider (D0058).
*   **Auth P5** Audit log + user management + per-project scoping (D0059–D0060).

## Install
`uv venv && uv pip install -e ".[dev]"`. Env: `~/.config/orchid/.env`. `ANTHROPIC_API_KEY` required. `JWT_SECRET` required for web auth.
