# CLI Auth Integration Plan

## Problem

CLI runs are single-user local only — no JWT, no vault injection, no budget enforcement,
no per-user MCP catalog ACLs. Multi-user features (vault credentials, budget tracking,
scheduler, audit log, user management) are web/API only.

Specific gaps:
- `_cmd_auto` / `_cmd_run_task` never call `vault_env_context` — vault credentials unavailable to CLI task runs
- `orchid mcp ls/call` uses anonymous `MCPManager.connect()`, bypasses catalog ACLs
- Claude API costs from CLI never recorded in `budget_used_usd`
- `BudgetGuard` only wired in `cron/executor.py` — CLI runs ignore LLM/CPU limits
- No `orchid login`, no `orchid whoami`, no concept of "which user is running this"

---

## Phase 1 — `orchid login` / CLI session store (foundation)

Add `orchid login [--username] [--server URL]` and `orchid logout`.

- Validate credentials directly against `FileUserStore` (no running server required for local use)
- Store access token + refresh token in `~/.config/orchid/cli_session.json`, mode 0600
- `orchid whoami` prints current user, role, budget remaining
- `_load_cli_session()` helper used by phases 2–4; returns `None` gracefully when no session exists

**Files:** new `orchid/interfaces/cli_auth.py`, new subcommands in `orchid/interfaces/cli.py`

**Decision needed:** local `FileUserStore` validation vs POST to running server.
Local is simpler and works without `orchid serve` running. Server-based works for remote instances.

---

## Phase 2 — Vault injection in CLI task runs

When `cli_session.json` exists and `ORCHID_VAULT_KEY` is set:

- Resolve `user_id` from stored session
- Wrap `_cmd_auto` and `_cmd_run_task` in `vault_env_context(user_id)` before calling `Orchestrator`
- Provider env vars from vault become available via existing `get_env()` in execution paths
- No change to `Orchestrator` — `get_env()` already reads thread-local overrides

**Files:** `orchid/interfaces/cli.py` lines ~329 (`_cmd_auto`) and ~408 (`_cmd_run_task`)

**Graceful degradation:** no session → skip silently, no breaking change for single-user machines.

---

## Phase 3 — Budget recording for CLI runs

After `_cmd_auto` / `_cmd_run_task` returns:

- Read accumulated `_exec_local.cost_usd` thread-local (already set by providers)
- Call `BudgetGuard(user_id).record(cost)` + `BudgetGuard(user_id).record_cpu(elapsed)`
- Call `BudgetGuard(user_id).check()` before run — CLI respects user limits if set
- Graceful degradation: no session → skip silently

**Files:** `orchid/interfaces/cli.py` — wrap existing run commands; no new modules needed.

---

## Phase 4 — `orchid mcp` uses per-user catalog

When session present:

- `orchid mcp ls` and `orchid mcp call` call `MCPManager.connect_for_user(user_id, role, catalog_store, vault_store, users_dir)` instead of anonymous `connect()`
- User sees only servers they're allowed; admin sees all
- Falls back to anonymous `connect()` when no session

**Files:** `orchid/interfaces/cli.py` `ls()` (~line 1946) and `call()` (~line 1982)

---

## Phase 5 — `orchid user` / `orchid admin` subcommands (optional)

Thin CLI wrappers around existing API endpoints for scripting and ops use.

- `orchid user list` / `orchid user invite EMAIL` / `orchid user budget reset UID` (admin only)
- `orchid scheduler list` / `orchid scheduler run TASK_ID`
- `orchid audit [--limit 50] [--user UID]`

These are `httpx` calls to the running server — zero new server-side code.
Requires Phase 1 (session token) to authenticate.

---

## Dependency order

```
Phase 1 (login/session)
  └── Phase 2 (vault injection)
  └── Phase 3 (budget recording)
  └── Phase 4 (MCP catalog)
        └── Phase 5 (user/admin subcommands) — independent, just needs Phase 1
```

## Open questions

1. **Phase 1 auth method:** local `FileUserStore` validation (no server needed) or POST to `localhost:7842`?
2. **Single-user opt-out:** no `cli_session.json` → phases 2–4 skip silently (no breaking change). Confirm this is the desired behaviour.
3. **Scope:** implement all 5 phases, or just 1–4?
