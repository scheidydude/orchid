# HANDOFF.md
_Updated: 2026-05-29. Previous HANDOFF archived as `HANDOFF-archive-2026-05-29-1043.md`._

---

## 1. Mission

Orchid is a standalone AI agent orchestration + multi-user agentic OS. The multi-user system (Phases 1–5, post-v3 hardening) was already complete. Two sessions ago we wired CLI auth integration (Phases 1–4). This session completed:

- **Phase 5** — `orchid user list/invite/budget-reset`, `orchid scheduler list/run`, `orchid audit` CLI wrappers
- **Q3** — Fixed `TestTaskExecutorAgentTool` mock isolation (was hitting real local LLM)
- **Q4** — `/api/auth/me` now returns budget fields; `orchid whoami` displays them

---

## 2. Current State

### Working and verified at `7ba35b2`

**CLI auth integration — all 5 phases shipped:**

- `orchid login [--server URL] [--username]` — POSTs to `/api/auth/login`, stores session at `~/.config/orchid/cli_session.json` (mode 0600).
- `orchid logout` — revokes server-side, deletes session file.
- `orchid whoami` — GETs `/api/auth/me`; displays username, role, email, **budget used/limit, cpu used/limit**; falls back to cached info if server unreachable.
- `--mode auto` and `--run-task`: `BudgetGuard` check/record, `vault_env_context` wrapping.
- `orchid mcp ls/call`: uses `connect_for_user()` when logged in; anonymous `connect()` otherwise.
- **Phase 5** (`orchid user`, `orchid scheduler`, `orchid audit`): thin httpx wrappers around existing API endpoints; all require `get_valid_session()` for Bearer auth; degrade cleanly with "not logged in" message if no session.

**`/api/auth/me` now includes:**
`budget_usd`, `budget_used_usd`, `cpu_budget_seconds`, `cpu_used_seconds`

**`TestTaskExecutorAgentTool` (6 tests) now pass:**
- Root cause: `get_registry().resolve("base")` → `LocalProvider`, not `AnthropicProvider`. Tests patched `anthropic.Anthropic` but the wrong provider ran.
- Fix: all 6 tests mock `orchid.providers.registry.get_registry` to return an `AnthropicProvider` registry.
- `AnthropicProvider.complete_with_tools` now sets `is_error: True` on tool dispatch exceptions.
- `dispatch` in `_run_agent_tool` raises `ValueError` on unknown tool (instead of returning error string) so `is_error` propagates.
- Returns `"[truncated: reached max_iterations=N]"` when loop exhausts without `end_turn`.

**Test suite:** 916+ tests passing (excluding 3 known pre-existing flaky tests).

### Half-built / deferred

Nothing new. Q2 (merging project + catalog servers in `orchid mcp ls`) was explicitly declined.

### Next action for fresh session

No outstanding CLI auth work. Pick up from other areas or new features.

---

## 3. Decisions Made (and Why)

_(Prior decisions unchanged — see archived HANDOFF for Phase 1–4 rationale.)_

**Decision (Phase 5):** CLI wrappers use `get_valid_session()` + httpx calls to the running server — zero new server-side code.
- **Reason:** All endpoints already exist. CLI just needs auth and display logic.
- **Note:** `orchid user` commands are admin-only at the server level; the CLI shows a `[red]Admin role required.[/red]` message on 403.

**Decision (Q3):** Patch `orchid.providers.registry.get_registry` in tests, not `LocalProvider`.
- **Reason:** Patching the registry is minimal and doesn't change production code paths. The real fix is that `_run_agent_tool` always called `registry.resolve("base")` → local LLM. Tests verified behavior against the Anthropic provider which owns the tool loop logic being tested.

**Decision (Q2 = no):** `orchid mcp ls` when logged in shows ONLY catalog/private servers, not merged with `.orchid.yaml` project servers.
- **Status:** Intentional. If needed later: merge `_server_config` dicts before calling `connect_for_user()`. Low effort.

---

## 4. Architecture & Key Files

### Modified this session

```
orchid/interfaces/cli.py        +_api() helper, +_require_session() helper
                                +user_app (user list/invite/budget-reset)
                                +scheduler_app (scheduler list/run)
                                +audit command
                                whoami: displays budget/cpu usage lines

orchid/interfaces/web_server.py /api/auth/me: adds budget_usd, budget_used_usd,
                                cpu_budget_seconds, cpu_used_seconds to response

orchid/providers/anthropic.py   complete_with_tools: is_error=True on dispatch
                                exception; "[truncated: reached max_iterations=N]"
                                fallback message

orchid/cron/executor.py         dispatch(): raise ValueError on unknown tool
                                (was: return error string — never propagated is_error)

tests/test_cron_executor.py     TestTaskExecutorAgentTool: _anth_reg() helper +
                                patch get_registry in all 6 LLM-touching tests;
                                updated is_error assertion to search all messages
```

### Should NOT be touched

```
orchid/web/server.py            DEAD FILE — never loaded by orchid serve.
orchid/interfaces/portal/vite.config.js   base: '/app/' is load-bearing.
orchid/auth/jwt.py              Crypto params settled.
.claude/settings.local.json    Local harness config. Never commit.
```

---

## 5. Gotchas & Hard-Won Knowledge

**`get_registry().resolve("base")` → `LocalProvider`, NOT `AnthropicProvider`.**
`_AGENT_DEFAULTS["base"] = "local"` in `registry.py`. Any test that expects `anthropic.Anthropic` to be called must also mock `get_registry`. This burned 6 tests silently.

**`mock_client.messages.create.call_args_list[N].kwargs["messages"]` is a reference, not a copy.**
After the call, `msgs.append(...)` mutates the same list that was passed. `second_msgs[-1]` may be the *post-call* final assistant message, not the last message at call time. Search all messages for tool_results instead of using index `-1`.

_(All prior gotchas from Phase 1–4 still apply — see archived HANDOFF.)_

**Pre-existing test hangs (not caused by this work):**
- `test_parallel_runner.py::TestExecuteTaskWithSemaphore::test_semaphore_acquisition_failure_sets_blocked` — `threading.Semaphore(0).acquire()` blocks forever.
- `test_worktree.py::TestWorktreeManager` — fd exhaustion when run at end of 1700+ test suite.
- `test_cron_executor.py::TestTaskExecutorAgentTool` — **FIXED this session.**

---

## 6. Conventions In Play

- **Caveman mode active** — responses are terse, fragments OK. `stop caveman` to revert.
- **Imports are lazy throughout `cli.py`** — all heavy imports inside function bodies, not at module level. Keep this pattern for new CLI subcommands. `_api()` and `_require_session()` are small helpers that import `httpx`/`cli_auth` inline.
- **Errors in auth helpers swallowed silently** — phases 2–4 degrade to no-op if session missing. Phase 5 commands exit 1 with an error message on auth failure.
- **Commit after each phase; push after each session.** Done — `7ba35b2` pushed.
- **Test with `source .venv/bin/activate && python -m pytest tests/ -q --tb=short -k "not test_semaphore and not test_worktree"`**
- **CLAUDE.md is the canonical architecture reference** — update when adding new modules or changing behavior.

---

## 7. Open Questions

1. **`orchid mcp` showing both project and catalog servers when logged in?** User said no — current behavior (catalog/private only when logged in) is intentional.

2. **The `test_providers.py` failures** (6 tests: `test_resolve_name_agent_type_default`, etc.) are pre-existing — `_AGENT_DEFAULTS` values don't match what the tests expect. Not caused by this work, not fixed.

3. **`/api/auth/me` budget fields** — now included. `orchid whoami` displays them. Admin portal's `/api/auth/users` already included them; this closes the gap for the user-facing endpoint.

---

## 8. Do Not Touch

- `orchid/web/server.py` — dead file, never loaded
- `orchid/interfaces/portal/vite.config.js` `base: '/app/'` — load-bearing
- `orchid/auth/jwt.py` — crypto settled
- `.claude/settings.local.json` — local harness config, never commit
- The cron executor's `_exec_local.cost_usd` thread-local pattern — budget recording in the cron path already works correctly; don't conflate it with the CLI path

---

## 9. Resume Command

> Read `HANDOFF.md`. CLI auth integration (all 5 phases) is complete at commit `7ba35b2`. Phase 5 added `orchid user list/invite/budget-reset`, `orchid scheduler list/run`, `orchid audit`. `/api/auth/me` now returns budget fields. `TestTaskExecutorAgentTool` mock isolation fixed. No outstanding CLI auth work — pick up from other features. Run tests with `source .venv/bin/activate && python -m pytest tests/ -q -k "not test_semaphore and not test_worktree"`. Do not touch `orchid/web/server.py`. Commit between phases; push after session.
