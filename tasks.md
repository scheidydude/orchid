# Tasks

## Phase 7 — Formal Resource / Cost Scheduling

- [ ] **T200** Create `orchid/cost/` package with `__init__.py` and `ledger.py` `type:code_generate` `p1` `model:local`

Create two files.

**File 1: `orchid/cost/__init__.py`** — content: `# Cost tracking and scheduling`.

**File 2: `orchid/cost/ledger.py`** — define exactly one class.

**`CostLedger`**:
Constructor: `__init__(self, project_dir: Path | str)`. Sets:
- `self._project_dir = Path(project_dir)`
- `self._ledger_file = self._project_dir / ".orchid" / "cost_ledger.jsonl"`
- `self._lock = threading.Lock()`

**`record(self, provider: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None`**:
- Builds record dict: `{"ts": datetime.now(UTC).isoformat(), "date": date.today().isoformat(), "provider": provider, "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": round(cost_usd, 6)}`.
- Creates parent dir if missing: `self._ledger_file.parent.mkdir(parents=True, exist_ok=True)`.
- Acquires `_lock`, appends JSON line to file.
- Wraps in try/except, logs warning on failure.

**`daily_spend(self, provider: str) -> float`**:
- Reads ledger file (if missing, returns 0.0).
- Filters lines where `record["date"] == date.today().isoformat()` and `record["provider"] == provider`.
- Returns sum of `record["cost_usd"]`.
- Acquires `_lock` while reading.
- Returns 0.0 on any read error.

**`daily_tokens(self, provider: str) -> int`**:
- Same as `daily_spend` but sums `input_tokens + output_tokens`.
- Returns 0 on missing file or error.

**`budget_remaining(self, provider: str) -> float | None`**:
- Reads `cfg.get(f"cost.daily_budget_usd.{provider}", None)`.
- If None → returns None (no budget set).
- Returns `budget - self.daily_spend(provider)`. Can be negative.

Imports: `from __future__ import annotations`, `import json`, `import logging`, `import threading`, `from datetime import UTC, date, datetime`, `from pathlib import Path`, `from orchid import config as cfg`.
`logger = logging.getLogger(__name__)`.

- [ ] **T201** Create `orchid/cost/scheduler.py` `type:code_generate` `p1` `needs:T200` `model:local`

Create new file `orchid/cost/scheduler.py`. Define exactly one class.

**`CostAwareScheduler`**:
Constructor: `__init__(self, project_dir: Path | str)`. Creates `self._ledger = CostLedger(project_dir)`.

**`select_provider(self, candidates: list[str], task_type: str = "", task_priority: int = 2) -> str`**:
- `candidates` is an ordered list of provider names (e.g. `["anthropic", "local"]`) in preference order.
- Returns the first candidate that passes all checks, or the last candidate if all fail (never returns empty).
- Checks for each candidate in order:
  1. **Budget check**: if `_ledger.budget_remaining(provider)` is not None and <= 0 → skip (over budget).
  2. **Rate pressure check**: if `_is_rate_limited(provider)` → skip.
  3. **Local preference under pressure**: if `cfg.get("cost.prefer_local_under_pressure", False)` and provider != "local" and `_ledger.budget_remaining(provider)` is not None and `_ledger.budget_remaining(provider) < cfg.get("cost.local_fallback_threshold_usd", 1.0)` → skip and try next.
- If all candidates are skipped, return last candidate (fallback — must always run something).

**`_is_rate_limited(self, provider: str) -> bool`**:
- Reads `cfg.get(f"cost._rate_pressure.{provider}", False)`.
- Returns that value (bool).
- Rate pressure flags are set externally (by orchestrator on 429 responses) via `set_rate_pressure`.

**`set_rate_pressure(self, provider: str, limited: bool) -> None`**:
- This writes to a module-level dict `_rate_flags: dict[str, bool]` (not to config — config is read-only).
- Define `_rate_flags: dict[str, bool] = {}` at module level.
- `_is_rate_limited` reads from `_rate_flags.get(provider, False)` (not config).
- `set_rate_pressure` writes `_rate_flags[provider] = limited`.

**`record_usage(self, provider: str, input_tokens: int, output_tokens: int) -> None`**:
- Estimates cost using `cfg.get(f"cost.price_per_1k_tokens.{provider}.input", 0.0)` and `cfg.get(f"cost.price_per_1k_tokens.{provider}.output", 0.0)`.
- Computes `cost_usd = (input_tokens / 1000) * input_price + (output_tokens / 1000) * output_price`.
- Calls `self._ledger.record(provider, input_tokens, output_tokens, cost_usd)`.

Imports: `from __future__ import annotations`, `from pathlib import Path`, `from orchid import config as cfg`, `from orchid.cost.ledger import CostLedger`.

- [ ] **T202** Add cost config to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `model:local`

Read `orchid/orchid.defaults.yaml`. Append after the `agent_pool:` section added in T194:

```yaml

# Cost tracking and scheduling.
# enforce_budget: false = track only, never block execution.
# enforce_budget: true = skip providers that exceed daily_budget_usd.
cost:
  enforce_budget: false
  prefer_local_under_pressure: false
  local_fallback_threshold_usd: 1.0   # switch to local if cloud budget < this
  daily_budget_usd:
    anthropic: null    # null = no limit
    local: null        # local has no cost
  price_per_1k_tokens:
    anthropic:
      input: 0.003     # claude-sonnet approximate $/1k input tokens
      output: 0.015    # claude-sonnet approximate $/1k output tokens
    local:
      input: 0.0
      output: 0.0
```

- [ ] **T203** Wire `CostLedger` token recording into `orchid/orchestrator.py` after agent run `type:code_generate` `p1` `needs:T200,T201,T202` `model:local`

Read `orchid/orchestrator.py`. Find `_execute_task`. Search for the line where `agent.run(` is called — this produces the result string. Find the line immediately after the agent run completes (before the next `self.session.log_event` or task-complete event).

Make exactly one change: add the cost-tracking block immediately after `result = agent.run(...)`:

```python
# Record token usage for cost tracking
try:
    from orchid.cost.scheduler import CostAwareScheduler
    _cost_sched = CostAwareScheduler(self.session.project_dir)
    _in_tok = sum(getattr(m, "input_tokens", 0) for m in getattr(agent, "history", []))
    _out_tok = sum(getattr(m, "output_tokens", 0) for m in getattr(agent, "history", []))
    if _in_tok or _out_tok:
        _cost_sched.record_usage(decision.model, _in_tok, _out_tok)
except Exception as _cost_err:
    logger.debug("Cost tracking failed (non-fatal): %s", _cost_err)
```

Do not make any other changes.

**Verification (required before Final Answer):** Run:
```
bash("grep -n 'record_usage\|cost_sched\|CostAwareScheduler' orchid/orchestrator.py")
```
Expected: at least 3 lines. If fewer, the write failed. Re-read the section around `agent.run(` and retry. Only give Final Answer after grep confirms all 3 symbols.

- [ ] **T203b** Wire 429 rate-limit detection into `orchid/orchestrator.py` `type:code_generate` `p1` `needs:T203` `model:local`

Read `orchid/orchestrator.py`. Search for `ProviderUnavailableError` — find the `except ProviderUnavailableError` block inside `_execute_task`.

Make exactly one change: inside that except block, add the rate-limit flag:

```python
# Mark provider as rate-limited for cost scheduler
if "429" in str(e) or "rate" in str(e).lower():
    try:
        from orchid.cost.scheduler import set_rate_pressure
        set_rate_pressure(decision.model, True)
    except Exception:
        pass
```

Add these lines as the first thing inside the `except ProviderUnavailableError` block, before any existing exception handling.

**Verification (required before Final Answer):** Run:
```
bash("grep -n 'set_rate_pressure\|ProviderUnavailableError' orchid/orchestrator.py")
```
Expected: at least 2 lines. If `set_rate_pressure` is missing, write failed. Re-read the except block and retry. Only give Final Answer after grep confirms both symbols.

- [ ] **T204** Wire `CostAwareScheduler` into provider resolution in `orchid/orchestrator.py` `type:code_generate` `p1` `needs:T201,T202,T203b` `model:local`

Read `orchid/orchestrator.py`. Find `_resolve_provider(self, task)` (added in T178).

After the `decision` is set (at the end of `_resolve_provider`, before the `return` statement), add cost-aware provider selection:

```python
# Cost-aware provider override
if cfg.get("cost.enforce_budget", False) or cfg.get("cost.prefer_local_under_pressure", False):
    try:
        from orchid.cost.scheduler import CostAwareScheduler
        _cost_sched = CostAwareScheduler(self.session.project_dir)
        # Build candidate list: primary provider first, then "local" as fallback
        _candidates = [decision.model]
        if decision.model != "local":
            _candidates.append("local")
        _selected = _cost_sched.select_provider(
            candidates=_candidates,
            task_type=task.type,
            task_priority=task.priority,
        )
        if _selected != decision.model:
            logger.info(
                "Cost scheduler overrode provider %s → %s for task %s",
                decision.model, _selected, task.id,
            )
            decision = RouteDecision(
                model=_selected,
                reason="cost_scheduler",
                source="cost_scheduler",
            )
    except Exception as _ce:
        logger.debug("Cost scheduler failed (non-fatal): %s", _ce)

return decision.model, decision
```

Keep all existing offline mode logic intact — the cost override should be skipped when `self.offline_mode` is True. Add an early return guard:
```python
if self.offline_mode:
    return decision.model, decision  # skip cost override in offline mode
```
Place this right before the cost-aware block.

**Verification (required before Final Answer):** Run:
```
bash("grep -n 'cost.enforce_budget\|cost_scheduler\|_resolve_provider' orchid/orchestrator.py")
```
Expected: at least 3 lines — the config check, the RouteDecision override, and the method definition. If fewer than 3, write failed. Re-read `_resolve_provider` and retry. Only give Final Answer after grep confirms all symbols.

- [ ] **T205** Create `tests/test_cost_ledger.py` `type:code_generate` `p1` `needs:T200` `model:local`

Create file `tests/test_cost_ledger.py`. Write exactly 4 test functions using `tmp_path`.

```python
import json
from datetime import date
from orchid.cost.ledger import CostLedger
```

**`test_record_creates_file(tmp_path)`**: create `CostLedger(tmp_path)`. Call `record("anthropic", 100, 50, 0.001)`. Assert `(tmp_path / ".orchid" / "cost_ledger.jsonl").exists()`.

**`test_daily_spend_sums_today(tmp_path)`**: record two entries for `"anthropic"` with costs `0.01` and `0.02`. Call `daily_spend("anthropic")`. Assert result is approximately `0.03` (within 1e-6).

**`test_daily_spend_ignores_other_providers(tmp_path)`**: record `"anthropic"` cost 0.05 and `"local"` cost 0.01. Assert `daily_spend("anthropic") == pytest.approx(0.05)` and `daily_spend("local") == pytest.approx(0.01)`.

**`test_budget_remaining_returns_none_when_no_budget(tmp_path)`**: patch `cfg.get` to return `None` for budget key. Assert `budget_remaining("anthropic")` returns `None`.

- [ ] **T206** Create `tests/test_cost_scheduler.py` `type:code_generate` `p1` `needs:T201` `model:local`

Create file `tests/test_cost_scheduler.py`. Write exactly 4 test functions using `tmp_path` and `unittest.mock.patch`.

```python
from unittest.mock import patch, MagicMock
from orchid.cost.scheduler import CostAwareScheduler, set_rate_pressure, _rate_flags
```

Clear `_rate_flags` dict in each test (e.g. `_rate_flags.clear()`) to prevent state leakage.

**`test_select_provider_returns_first_candidate_by_default(tmp_path)`**: patch ledger's `budget_remaining` to return None (no budget). Call `select_provider(["anthropic", "local"])`. Assert returns `"anthropic"`.

**`test_select_provider_skips_over_budget(tmp_path)`**: patch `budget_remaining("anthropic")` to return `-0.5` (over budget). Call `select_provider(["anthropic", "local"])`. Assert returns `"local"`.

**`test_select_provider_skips_rate_limited(tmp_path)`**: call `set_rate_pressure("anthropic", True)`. Call `select_provider(["anthropic", "local"])`. Assert returns `"local"`. Then call `set_rate_pressure("anthropic", False)`. Assert `select_provider(["anthropic", "local"])` returns `"anthropic"`.

**`test_select_provider_fallback_when_all_fail(tmp_path)`**: patch all candidates to fail budget and rate checks. Call `select_provider(["anthropic", "local"])`. Assert returns last candidate `"local"` (never raises, always returns something).

- [ ] **T207** Review cost scheduling implementation `type:code_review` `p1` `needs:T205,T206,T204`

Review files: `orchid/cost/ledger.py`, `orchid/cost/scheduler.py`, `orchid/orchestrator.py` (T203, T203b, and T204 additions only).

Check for exactly these issues:
1. **Ledger lock scope** — does `daily_spend()` hold `_lock` while iterating lines? If a write (`record()`) arrives mid-read, could the reader see a partial line? Report PASS if read is done inside `with self._lock:`.
2. **`_rate_flags` global state in tests** — `_rate_flags` is module-level. If tests run in parallel, one test's `set_rate_pressure` could affect another. Report PASS if test file clears `_rate_flags` before each test.
3. **Cost override skips offline mode** — does `_resolve_provider` skip the cost override when `self.offline_mode` is True? Report PASS or FAIL with line number.
4. **Token extraction from agent history** — `getattr(m, "input_tokens", 0)` assumes `Message` objects have `input_tokens` attribute. Does `orchid.tools.models.Message` have this field? If not, the sum will always be 0. Report FAIL if field is absent.
5. **`set_rate_pressure` import path** — in orchestrator.py, is `set_rate_pressure` imported as a module-level function (not method)? Report PASS or FAIL.

- [ ] **T208** Fix issues found in T207 and add token fields if missing `type:code_generate` `p1` `needs:T207` `model:local`

Read T207 review results. Apply fixes:

For issue 1 (lock scope): if `daily_spend()` reads outside `_lock`, wrap the entire file read-and-parse block with `with self._lock:`.

For issue 4 (token fields on Message): Read `orchid/tools/models.py`. Find the `Message` dataclass or class. If `input_tokens` and `output_tokens` fields are absent, add them:
```python
input_tokens: int = 0
output_tokens: int = 0
```
Then find where `Message` objects are created after API calls (in the `call()` function or equivalent). If the provider response includes token counts, populate these fields. If the provider response format doesn't include token counts, leave them at 0 and add a comment: `# Populated by provider if available`.

For issue 3 (offline mode guard): if the guard is missing, add it as specified in T204 task description.

Apply only fixes for flagged FAILs. Issue 2 (test state) is not a code bug — do not change production code for it. If no production FAILs, write `Final Answer: Applied Message token fields only.`

## DONE

- [x] **T198** Review agent pool implementation `type:code_review` `p1` `needs:T197`
- [x] **T199** Fix issues found in T198 `type:code_generate` `p1` `needs:T198` `model:local`
- [x] **T197** Create `tests/test_agent_pool.py` `type:code_generate` `p1` `needs:T193` `model:local`
- [x] **T193** Create `orchid/agent_pool.py` `type:code_generate` `p1` `model:local`
- [x] **T194** Add agent pool config to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `model:local`
- [x] **T195** Wire `AgentPool` into `BackgroundRunner._run()` in `orchid/runner.py` `type:code_generate` `p1` `needs:T193,T194` `model:local`
- [x] **T196** Wire `AgentPool` into `AgentDelegator.delegate()` in `orchid/agents/delegator.py` `type:code_generate` `p1` `needs:T193` `model:local`
- [x] **T192** Fix issues found in T191 `type:code_generate` `p1` `needs:T191` `model:local`
- [x] **T191** Review dynamic spawning implementation `type:code_review` `p1` `needs:T190,T188b`
- [x] **T186** Add `inject_task` method to `orchid/session.py` `type:code_generate` `p1` `model:local`
- [x] **T187** Create `orchid/tools/task_injection.py` `type:code_generate` `p1` `needs:T186` `model:local`
- [x] **T188** Add `spawn_task` to `_make_project_tools` in `orchid/agents/base.py` `type:code_generate` `p1` `needs:T187` `model:local`
- [x] **T188b** Wire `set_active_session` into `_execute_task` in `orchid/orchestrator.py` `type:code_generate` `p1` `needs:T188` `model:local`
- [x] **T189** Add `spawn_task` description to DeveloperAgent system prompt `type:code_generate` `p1` `needs:T188b` `model:local`
- [x] **T190** Create `tests/test_task_injection.py` `type:code_generate` `p1` `needs:T186,T187` `model:local`
- [x] **T185** Fix issues found in T184 `type:code_generate` `p1` `needs:T184` `model:local`
- [x] **T184** Review parallelism implementation `type:code_review` `p1` `needs:T182,T183`
- [x] **T183** Create `tests/test_parallel_runner.py` `type:code_generate` `p1` `needs:T176,T177,T178,T179,T180` `model:local`
- [x] **T176** Create `orchid/scheduler.py` `type:code_generate` `p1` `model:local`
- [x] **T177** Add threading lock to `orchid/session.py` `type:code_generate` `p1` `model:local`
- [x] **T178** Extract `_resolve_provider` method from `_execute_task` in `orchid/orchestrator.py` `type:code_generate` `p1` `model:local`
- [x] **T179** Add provider semaphores to `BackgroundRunner` in `orchid/runner.py` `type:code_generate` `p1` `needs:T177` `model:local`
- [x] **T180** Rewrite `BackgroundRunner._run()` loop for parallel dispatch `type:code_generate` `p1` `needs:T176,T177,T178,T179` `model:local`
- [x] **T181** Add `runner.provider_concurrency` to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `model:local`
- [x] **T182** Create `tests/test_scheduler.py` `type:code_generate` `p1` `needs:T176` `model:local`
- [x] **T170** Create `orchid/worktree.py` `type:code_generate` `p1` `model:local`
- [x] **T171** Add worktree config to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `model:local`
- [x] **T172** Wire WorktreeManager into `AgentDelegator.delegate()` `type:code_generate` `p1` `needs:T170,T171` `model:local`
- [x] **T173** Create `tests/test_worktree.py` `type:code_generate` `p1` `needs:T170` `model:local`
- [x] **T174** Review worktree implementation `type:code_review` `p1` `needs:T173`
- [x] **T175** Fix issues found in T174 `type:code_generate` `p1` `needs:T174` `model:local`
- [x] **T163** Create `orchid/tools/git.py` `type:code_generate` `p1` `model:local`
- [x] **T164** Register git tools in `_make_project_tools` in `orchid/agents/base.py` `type:code_generate` `p1` `needs:T163` `model:local`
- [x] **T165** Add git tools to DeveloperAgent `allowed_tools` and system prompt `type:code_generate` `p1` `needs:T164,T156` `model:local`
- [x] **T166** Add `git_tools_enabled` config to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `model:local`
- [x] **T166b** Wrap git tool registration in config guard in `orchid/agents/base.py` `type:code_generate` `p1` `needs:T164,T166` `model:local`
- [x] **T167** Create `tests/test_git_tools.py` `type:code_generate` `p1` `needs:T163` `model:local`
- [x] **T168** Review git integration `type:code_review` `p1` `needs:T167,T166b`
- [x] **T169** Fix issues found in T168 `type:code_generate` `p1` `needs:T168` `model:local`
- [x] **T152** Wire circuit breaker into HTTP hook handler in `orchid/hooks/loader.py` `type:code_generate` `p1` `needs:T151` `model:local`
- [x] **T153** Create `orchid/hooks/audit.py` `type:code_generate` `p1` `model:local`
- [x] **T154** Wire audit logging into shell hook handler in `orchid/hooks/loader.py` `type:code_generate` `p1` `needs:T152,T153` `model:local`
- [x] **T155** Add `allowed_tools` filtering to `BaseAgent` in `orchid/agents/base.py` `type:code_generate` `p1` `model:local`
- [x] **T156** Set `allowed_tools` on TesterAgent, ReviewerAgent, ResearcherAgent `type:code_generate` `p1` `needs:T155` `model:local`
- [x] **T157** Add permissions and circuit-breaker config to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `needs:T155` `model:local`
- [x] **T158** Create `tests/test_circuit_breaker.py` `type:code_generate` `p1` `needs:T151` `model:local`
- [x] **T159** Create `tests/test_hook_audit.py` `type:code_generate` `p1` `needs:T153` `model:local`
- [x] **T160** Create `tests/test_agent_permissions.py` `type:code_generate` `p1` `needs:T155,T156` `model:local`
- [x] **T161** Review Phase 1 implementation `type:code_review` `p1` `needs:T158,T159,T160,T157`
- [x] **T162** Fix issues found in T161 `type:code_generate` `p1` `needs:T161` `model:local`
- [x] **T151** Create `orchid/hooks/circuit_breaker.py` `type:code_generate` `p1` `model:local`
- [x] **T150** Gap-closure sprint rollup `type:rollup` `p1` `rollup:T092,T093,T094,T095,T096,T097,T098,T099,T100,T101,T102,T103,T104,T105,T106,T107,T108,T109,T110,T111,T112,T113,T114,T115,T116,T117,T118,T119,T120,T121,T122,T123,T124,T125,T126,T127,T128,T129,T130,T131,T132,T133,T134,T135,T136,T137,T138,T139,T140,T141,T142,T143,T144,T145,T146,T147,T148,T149` `output:GAP-CLOSURE-REPORT.md`
- [x] **T124** Create `tests/test_mcp_integration.py`. Write exactly 1 test function. Start a real Python subprocess as a minimal MCP server using `python3 -c "<inline script>"`. The inline script must: listen on stdin, respond to `initialize` with `{"jsonrpc":"2.0","id":0,"result":{}}`, respond to `tools/list` with one tool named `echo`, respond to `tools/call` with `{"content": arguments["msg"], "isError": false}`. Test: `StdioMCPClient.connect()`, `list_tools()` returns `[MCPTool("echo",…)]`, `call_tool("echo", {"msg":"hello"})` returns `MCPResult(content="hello")`. Use `pytest.mark.skipif(sys.platform=="win32", reason="POSIX only")`. `type:test_write` `p1` `needs:T107`
- [x] **T088** Fix Discussion panel focus: after AI responds in the Discussion tab the message input loses focus and clicking it doesn't restore it. User has to leave the panel and come back. Fix: after each AI response completes, programmatically re-focus the message input using inputRef.current?.focus(). Also when the AI presents numbered options in its response, clicking an option fills the input but does not focus it — add focus() call after filling the input value. `type:code_generate` `p1`
- [x] **T089** Add loading indicator to Discussion panel when PM agent is generating artifacts: after user types 'done' or agent says it will generate REQUIREMENTS.md/ARCHITECTURE.md, show a visible loading state — spinner, progress message like 'Generating REQUIREMENTS.md...', and disable the input with 'Working...' placeholder. The backend already sends status callbacks via WebSocket (advance_status events) — ensure the frontend is listening for these and displaying them. This was implemented in T053 but may have regressed. `type:code_generate` `p1`
- [x] **T090** Fix lifecycle phase display: orchid --phase and Web UI phase indicator should not list the current phase as an advancement target in 'Can advance to:'. In gates.py or lifecycle.py, filter out the current phase from the list of possible next phases. Also verify the phase indicator in the Web UI PhaseIndicator component does not show the current phase as clickable/next. `type:code_generate` `p1`
- [x] **T087** Add per-agent provider overrides to .orchid.yaml: extend provider registry to support named agent overrides (discussion, product_manager, project_manager, developer, reviewer, orchestrator) in the providers: section of .orchid.yaml. These override type defaults but are overridden by CLI --provider flags. Update orchid.defaults.yaml with commented examples. Update DiscussionAgent, ProductManagerAgent, ProjectManagerAgent to check their named provider key before falling back to type default. This allows e.g. providers: discussion: local to route all PM planning through local model without --offline flag. `type:code_generate` `p1`
- [x] **T086** Create docs/pm-guide.md: a comprehensive PM walkthrough guide covering the full idea-to-execution pipeline in Orchid V2. Include: 1) Overview of the PM workflow (Discussion → Requirements → Planning → Execution → Review), 2) Starting a new project via Web UI New Project Wizard with screenshot placeholder, 3) The Discussion phase — chatting with the AI to refine requirements with screenshot placeholder, 4) Reviewing generated REQUIREMENTS.md and ARCHITECTURE.md in the Planning tab with screenshot placeholder, 5) Approving the plan to move to execution with screenshot placeholder, 6) Monitoring execution via the PM Dashboard — Milestone Progress, Dependency Graph, Session Burndown, Task Timing with screenshot placeholders for each, 7) Understanding task statuses (TODO/IN_PROGRESS/DONE/BLOCKED/SKIP), 8) Using Telegram and Slack for mobile project monitoring — /orchid_projects, /orchid_switch, /orchid_approve commands, 9) Reading the rollup milestone summaries (MILESTONE-1.md etc), 10) Glossary of terms (task types, phases, agents). Use [SCREENSHOT: description] placeholders throughout. Write in plain English for a non-technical PM audience. `type:draft` `p1`
- [x] **T075** Fix Planning tab artifact panels: text content in Requirements, Architecture, Milestones and tasks.md tabs is not scrollable when it exceeds the viewport. Fixed: min-height:0 on flex chain (.artifact-panel/body/view/content), overflow:hidden on panel-body when Planning active, wrapper divs for READY/EXECUTING/COMPLETE phases get proper flex constraints. `type:code_generate` `p1`
- [x] **T074** Planning tab: show completed phase artifacts in read-only mode regardless of current phase. When project is in EXECUTING or COMPLETE phase, the Requirements, Architecture, Milestones and Tasks tabs should still display their content as read-only. Currently they show 'Project is executing' instead of the artifact content. Only hide/disable editing — never hide the content itself. `type:code_generate` `p1`
- [x] **T072** Fix Planning tab artifact panels: Requirements, Architecture, Milestones and tasks.md tabs should scroll independently even when not in edit mode. Added overflow:hidden/padding:0 to panel-body when Planning tab is active; existing flex chain now propagates height correctly so artifact-content can scroll. `type:code_generate` `p1`
- [x] **T073** Add SKIP task status to Orchid: orchid task skip --id T015 --project . marks task as skipped (shown as [~] in tasks.md). Skipped tasks are excluded from auto mode runs but count as satisfied for dependencies. Added Skip button to Web UI task board. `type:code_generate` `p1`
- [x] **T069** Add --run-task flag to CLI: orchid --project . --run-task T015 executes a single specific task. Added ▶ Run button to each task row in Web UI. Added POST /api/projects/{id}/tasks/{task_id}/run endpoint. `type:code_generate` `p1`
- [x] **T062** Fix Slack channel routing: added debug logging to _resolve_project and _get_project_for_channel showing channel_id received vs map contents. `type:code_generate` `p1`
- [x] **T059** Review the prompt caching implementation in orchid/providers/anthropic.py and confirm cache_control blocks are correctly applied `type:review` `p1`
- [x] **T058** Review the prompt caching implementation in orchid/providers/anthropic.py and confirm cache_control blocks are correctly applied `type:review` `p1`
- [x] **T057** Write a one-line comment to README.md describing Orchid V2 `type:draft` `p1`
- [x] **T056** Write a brief V2 feature summary to V2-SUMMARY.md covering: lifecycle phases, strategic agents, web UI planning tab, prompt caching `type:draft` `p1`
- [x] **T053** Fix DiscussionPanel loading state: when agent says it's ready to generate artifacts there is no visual indicator that work is happening. Add: 1) A loading spinner/progress bar when PM agent is running after 'done' is typed. 2) Status messages like 'Generating REQUIREMENTS.md...' and 'Generating ARCHITECTURE.md...' streamed via WebSocket. 3) Disable the input and show 'Working...' while agents are running. 4) Show a success banner when artifacts are ready. `type:code_generate` `p1`
- [x] **T050** Fix Planning tab scroll: content is not scrollable, text gets cut off. Check overflow CSS on PlanningTab, DiscussionPanel and ArtifactPanel components — add overflow-y: auto and appropriate max-height or height: 100% to allow scrolling. `type:code_generate` `p1`
- [x] **T051** Fix Planning tab scroll: content not scrollable in DiscussionPanel, ArtifactPanel and ApprovalPanel — add overflow-y:auto and proper height constraints so all content is reachable `type:code_generate` `p1`
- [x] **T052** Fix DiscussionPanel chat input focus: after sending a message the input loses focus and clicking it doesn't restore focus. After agent response is received, automatically re-focus the input element using inputRef.current.focus(). Also ensure clicking anywhere in the input area triggers focus. `type:code_generate` `p1`
- [x] **T046** Check all Python files in orchid/ for syntax errors using py_compile `type:review` `p1`
- [x] **T047** Check all imports in orchid/ are resolvable `type:review` `p1`
- [x] **T048** Verify test suite passes: run pytest tests/ and report results `type:review` `p1`
- [x] **T049** Orchid health rollup `type:rollup` `p1` `rollup:T046,T047,T048` `output:HEALTH-REPORT.md`
- [x] **T041** Add post-write verification to tools/filesystem.py: after writing a .js file automatically run 'node --input-type=module --eval "import('./file.js')"' to catch syntax errors and missing imports. After writing a .py file run 'python3 -m py_compile file.py'. Return verification result as part of the write_file observation so the agent can self-correct immediately. `type:code_generate` `p1`
- [x] **T042** Add new tool tools/consistency.py with check_imports(project_path) function: scan all .js files for import statements, verify each imported file exists at the expected path, return list of broken imports as {file, import, expected_path, exists}. Also scan .py files for imports and verify modules exist. Add 'Action: check_imports[path]' to ReAct parser. Reviewer agent should call this automatically at the end of each session. `type:code_generate` `p1`
- [x] **T040** Move Orchid machine-level config to XDG standard location ~/.config/orchid/.env — 1) load_dotenv() should search in order: cwd, ~/.config/orchid/.env, ~/LocalAI/orchid/.env (legacy fallback). 2) Create scripts/setup-config.sh that copies .env to ~/.config/orchid/.env and sets permissions 600. 3) Update orchid-serve.service EnvironmentFile to point to ~/.config/orchid/.env. 4) Update .env.example and README with new location. 5) After fixing run uv tool install . --force `type:code_generate` `p1`
- [x] **T038** Fix web server run trigger: when starting an agent run via POST /api/projects/{project_id}/run the project path passed to BackgroundRunner must be the absolute filesystem path from the project registry, not a path relative to the orchid working directory. Reproduce by triggering a run from the Web UI and checking where write_file calls resolve to. `type:code_generate` `p1`
- [x] **T036** Fix discovery.py: skip inotify watch setup for non-existent watch dirs instead of crashing. Also add exclude dirs to watchdog Observer to prevent watching .venv, node_modules, .git etc (inotify watch limit) `type:code_generate` `p1`
- [x] **T035** Add exponential backoff with jitter to AnthropicProvider.complete() for 429 rate limit errors — wait up to 60s between retries, max 3 retries, log warning on each retry `type:code_generate` `p1`
- [x] **T033** Fix offline mode: hot memory compression should use local provider when --offline flag is set, not call Claude API `type:code_generate` `p1`
- [x] **T032** Simple hello world function `type:code_generate` `p1`
- [x] **T031** Write a haiku about distributed systems `type:draft` `p1`
- [x] **T029** Test Web UI live task creation `type:draft` `p1`
- [x] **T025** Dependency test parent task `type:draft` `p1`
- [x] **T026** Dependency test child task `type:draft` `p1`
- [x] **T024** Write a complex regex parser for extracting structured data from session logs `type:code_generate` `p1`
- [x] **T023** Archive all completed tasks to tasks.md archive section now `type:code_generate` `p1`
- [x] **T022** Investigate and fix chunking producing oversized token payloads - chunks exceeding 1024 tokens despite chunk_size=400 word setting. Likely word-based chunking not accounting for tokenization overhead. Switch to token-based chunking with hard cap at 800 tokens. `type:code_generate` `p1`
- [x] **T017** Fix delegations counter not persisting in session status display `type:code_generate` `p1`
- [x] **T018** Fix D0011 truncating in CLAUDE.md compression - root cause is compression threshold too aggressive for growing decisions list `type:code_generate` `p1`
- [x] **T021** Run full test suite and fix any failing tests `type:code_generate` `p1`
- [x] **T014** Research best practices for Python async context managers, then implement one in orchid/session.py for safe session lifecycle management `type:code_generate` `p1`
- [x] **T011** Fix developer agent prompt to use delegate action for research-first tasks `type:code_generate` `p1`
- [x] **T012** Fix decisions.json Extra data parse error - persists after T008 `type:code_generate` `p1`
- [x] **T010** Research the best approach for implementing a retry mechanism in httpx, then implement a retry wrapper in orchid/tools/models.py using that approach `type:code_generate` `p1`
- [x] **T007** Filter ad results from DuckDuckGo backend (skip results with y.js URLs) `type:code_generate` `p1`
- [x] **T008** Fix decisions.json parse error - likely JSON Lines vs single JSON document format mismatch `type:code_generate` `p1`
- [x] **T002** Hook LLM summarizer into session compression `type:code_generate` `p1`
- [x] **T001** Review the session.py compression logic and suggest improvements `type:review` `p1`
- [x] **T139** Create `orchid/checkpoint/store.py`. Implement exactly this class: `type:draft` `p2`
- [x] **T140** Create `orchid/checkpoint/restore.py`. Implement exactly these two functions: `type:draft` `p2`
- [x] **T141** Extend `orchid/orchestrator.py` — capture checkpoint before each task. Read the file first. In `_execute_task` (line 192), find the line `self.session.update_task_status(task.id, TaskStatus.IN_PROGRESS)`. Add the following block **before** that line: `type:draft` `p2`
- [x] **T142** Extend `orchid/runner.py` — prune checkpoints at session end. Read the file first. In `BackgroundRunner._run()` `finally:` block, before `mcp_manager.stop_all()` (added in T114), add: `type:draft` `p2`
- [x] **T143** Extend `orchid/interfaces/cli.py` — add `--rewind`, `--resume`, and `--list-checkpoints` options to `main()`. Read the file first. Add these three option parameters to `def main(`: `type:draft` `p2`
- [x] **T144** Review `orchid/checkpoint/store.py` for exactly these 4 issues. Report PASS or FAIL with line number: `type:draft` `p2`
- [x] **T145** Review `orchid/checkpoint/restore.py` for exactly these 3 issues. Report PASS or FAIL with line number: `type:draft` `p2`
- [x] **T146** Create `tests/test_checkpoint_schema.py`. Write exactly these 3 test functions: `type:draft` `p2`
- [x] **T147** Create `tests/test_checkpoint_store.py`. Write exactly these 4 test functions using `tmp_path` pytest fixture: `type:draft` `p2`
- [x] **T148** Create `tests/test_checkpoint_restore.py`. Write exactly these 2 test functions using `tmp_path`: `type:draft` `p2`
- [x] **T149** Create `tests/test_checkpoint_integration.py`. Write exactly 1 test function using `tmp_path`: `type:draft` `p2`
- [x] **T138** Create `orchid/checkpoint/schema.py`. Also create empty `orchid/checkpoint/__init__.py` with content `# Session checkpoint`. Define exactly these dataclasses: `type:draft` `p2`
- [x] **T129** Extend `orchid/orchestrator.py` — emit task events via `stream_callback`. Read the file first. The existing `self.stream_callback` at line 136 already sends dicts. Extend `_execute_task` to also emit typed stream events using the emitter if set. Make exactly these changes: `type:draft` `p2`
- [x] **T137** Create `tests/test_stream_json_cli.py`. Write exactly 1 test function that invokes the CLI with `--output-format stream-json` using `subprocess.run`: `type:draft` `p2`
- [x] **T125** Create `orchid/output/events.py`. Also create empty `orchid/output/__init__.py` with content `# Stream output events`. Define exactly these dataclasses. All fields must have defaults so instances can be created with only the unique fields: `type:draft` `p2`
- [x] **T126** Create `orchid/output/emitter.py`. Define a protocol class and `NullEmitter`. No imports from `orchid.output.events` needed — accept any object with `to_json()`: `type:draft` `p2`
- [x] **T127** Create `orchid/output/ndjson_emitter.py`. Implement exactly: `type:draft` `p2`
- [x] **T128** Create `orchid/output/ws_emitter.py`. Implement exactly: `type:draft` `p2`
- [x] **T130** Extend `orchid/runner.py` — emit session-level events. Read the file first. In `BackgroundRunner._run()`, make exactly these changes: `type:draft` `p2`
- [x] **T131** Extend `orchid/interfaces/cli.py` — add `--output-format` option to the `main()` typer function and wire emitter into `_cmd_auto`. Read the file first. Make exactly these changes: `type:draft` `p2`
- [x] **T132** Extend `orchid/web/server.py` — add NDJSON streaming endpoint. Read the file first. Find the FastAPI app instance and existing `/api/projects/{project_id}/run` route (or equivalent run endpoint). Add a new route: `type:draft` `p2`
- [x] **T133** Review `orchid/output/events.py` for exactly these 3 issues. Report PASS or FAIL with line number: `type:draft` `p2`
- [x] **T134** Review `orchid/output/ndjson_emitter.py` for exactly these 2 issues. Report PASS or FAIL with line number: `type:draft` `p2`
- [x] **T135** Create `tests/test_output_events.py`. Write exactly these 3 test functions: `type:draft` `p2`
- [x] **T136** Create `tests/test_ndjson_emitter.py`. Write exactly these 3 test functions: `type:draft` `p2`
- [x] **T107** Create `orchid/mcp/stdio_client.py`. Implement exactly this class using `subprocess.Popen`: `type:draft` `p2`
- [x] **T108** Create `orchid/mcp/http_client.py`. Implement exactly this class using `httpx.Client` (sync): `type:draft` `p2`
- [x] **T109** Create `orchid/mcp/adapter.py`. Implement exactly this class: `type:draft` `p2`
- [x] **T110** Create `orchid/mcp/manager.py`. Implement exactly this class. It is fully synchronous: `type:draft` `p2`
- [x] **T111** Extend `orchid/orchid.defaults.yaml` — append MCP servers section. Read the file first to find its end. Append exactly this block at the bottom of the file: `type:draft` `p2`
- [x] **T112** Extend `orchid/config.py` — add one helper function. Read the file first. Append after the last function in the file: `type:draft` `p2`
- [x] **T113** Extend `orchid/orchestrator.py` — wire `MCPManager` into task execution. Read the file first. Make exactly two changes: `type:draft` `p2`
- [x] **T114** Extend `orchid/runner.py` — create and teardown `MCPManager` around the run loop. Read the file first. In `BackgroundRunner._run()` (line 70), make exactly these two changes: `type:draft` `p2`
- [x] **T115** Extend `orchid/interfaces/cli.py` — add `mcp` Typer sub-app with two commands. Read the file first. Find the pattern where sub-apps are registered (search for `app.add_typer`). Add a new sub-app with these two commands: `type:draft` `p2`
- [x] **T116** Review `orchid/mcp/stdio_client.py` for exactly these 4 issues. For each, report PASS or FAIL with the line number: `type:draft` `p2`
- [x] **T117** Review `orchid/mcp/http_client.py` for exactly these 4 issues. For each, report PASS or FAIL with the line number: `type:draft` `p2`
- [x] **T118** Review `orchid/mcp/adapter.py` for exactly these 3 issues. For each, report PASS or FAIL with the line number: `type:draft` `p2`
- [x] **T119** Create `tests/test_mcp_types.py`. Write exactly these 3 test functions, no fixtures needed: `type:draft` `p2`
- [x] **T120** Create `tests/test_mcp_stdio_client.py`. Write exactly these 4 test functions using `unittest.mock.patch`: `type:draft` `p2`
- [x] **T121** Create `tests/test_mcp_http_client.py`. Write exactly these 3 test functions using `respx` to mock httpx: `type:draft` `p2`
- [x] **T122** Create `tests/test_mcp_adapter.py`. Write exactly these 4 test functions. Use `unittest.mock.MagicMock` for the client: `type:draft` `p2`
- [x] **T123** Create `tests/test_mcp_manager.py`. Write exactly these 3 test functions using `unittest.mock.patch`: `type:draft` `p2`
- [x] **T106** Create `orchid/mcp/client.py`. Define exactly one ABC and one exception class: `type:draft` `p2`
- [x] **T105** Create `orchid/mcp/types.py`. Also create empty `orchid/mcp/__init__.py` with content `# MCP adapter layer`. Define exactly these three dataclasses and nothing else: `type:draft` `p2`
- [x] **T103** Unit tests `type:draft` `p2` `needs:T094`
- [x] **T092** Design and implement `type:draft` `p2`
- [x] **T093** Define hook event constants in `type:draft` `p2` `needs:T092`
- [x] **T094** Implement hook loader `type:draft` `p2` `needs:T093`
- [x] **T097** Wire hooks into session and phase transitions: fire `type:draft` `p2` `needs:T094`
- [x] **T098** Add hook config schema to `type:draft` `p2` `needs:T094`
- [x] **T099** Add CLI: `type:draft` `p2` `needs:T098`
- [x] **T100** Review hook registry and loader implementation: verify blocking hooks cannot deadlock the orchestrator, shell hooks are sandboxed by the existing shell allowlist, http hooks respect timeout, and hook errors are logged but never crash the agent loop. Check `type:draft` `p2` `needs:T094,T095,T096,T097`
- [x] **T101** Review hook integration points in `type:draft` `p2` `needs:T095,T096`
- [x] **T102** Unit tests `type:draft` `p2` `needs:T092,T093`
- [x] **T104** Integration tests `type:draft` `p2` `needs:T095,T096,T097`
- [x] **T095** Wire hooks into agent ReAct loop `type:draft` `p2` `needs:T094`
- [x] **T096** Wire hooks into task lifecycle `type:draft` `p2` `needs:T094`
- [x] **T091** Update docs/pm-guide.md: add section on configuring fully-local operation via .orchid.yaml providers overrides. Show example config for all-local PM planning and development with Claude only for final review. Explain the resolution order: CLI flag > project config > task annotation > defaults. `type:draft` `p2`
- [x] **T085** Add task metrics capture to orchestrator: on every task completion (done/blocked/skipped) write a structured record to .orchid/task_metrics.jsonl containing: task_id, title, status, iters_used, iters_max, duration_s, action counts by type, model, session_id, and blocker details (reason, last_action, last_error) when blocked. Always-on, no flag needed. Add GET /api/projects/{id}/metrics endpoint returning parsed metrics. This feeds the PM dashboard and replaces need for full trace.log in Web UI. `type:code_generate` `p2`
- [x] **T084** Add PM Dashboard view to Web UI: read-only project management view accessible from main navigation. Components: 1) Milestone Progress — milestone name, task count, completed/blocked/pending breakdown, completion %. 2) Dependency Graph — visual DAG of tasks showing dependencies (needs:), critical path highlighted, blocked tasks in red, completed in green, pending in grey. Use a lightweight JS graph library (d3 or cytoscape.js). 3) Session Burn-down — tasks completed per session over time, bar chart per session showing completed/blocked/skipped counts. 4) Phase Timeline — for V2 lifecycle projects show time spent in each phase (DISCUSSING/REQUIREMENTS/PLANNING/EXECUTING) as a horizontal timeline. 5) Task Timing — table of completed tasks sorted by duration (from trace.log if available, session logs otherwise) showing fastest and slowest tasks. All views are read-only. Add PM tab to main navigation alongside Tasks/Planning/Stream etc. `type:code_generate` `p2`
- [x] **T079** Add venv/Docker awareness to agent bash tool: when running pytest or python in a project directory, check for .venv/bin/python, venv/bin/python, or docker-compose.yml and use the appropriate runner. Add to CLAUDE.md template: if project has .venv use .venv/bin/python, if Docker use docker-compose exec. This prevents agents wasting iterations trying bare python3 which has no packages. `type:code_generate` `p2`
- [x] **T080** Add project environment detection to agents: at task start, check project root for docker-compose.yml, .venv/, venv/, package.json, Pipfile, pyproject.toml. Store detected environment in session context. Use this to skip runtime test execution when Docker is not running, and prefer syntax-only verification (py_compile, node --check) instead. Add environment: docker|venv|node|unknown field to .orchid.yaml that overrides auto-detection. `type:code_generate` `p2`
- [x] **T081** Add verify_syntax_only mode to agents: when agents.verify_syntax_only: true is set in .orchid.yaml, DeveloperAgent skips all runtime test execution (pytest, jest, make test, docker exec) and only runs syntax checks (py_compile, node --check, tsc --noEmit). Add this setting to orchid.defaults.yaml defaulting to false. Update agent system prompt to include the current verify mode so the model knows not to attempt runtime tests. `type:code_generate` `p2`
- [x] **T082** Add TesterAgent: new agent class orchid/agents/tester.py focused solely on verification. Detects project environment (docker-compose.yml → docker, .venv/ → venv, package.json → node). Knows how to run: pytest, jest, make test, docker compose exec. Auto-injected by orchestrator after each code_generate task completes using the task manifest file list. Returns structured result: {passed: bool, tests_run: int, failures: [], files_checked: []}. Route type:verify tasks to TesterAgent. `type:code_generate` `p2`
- [x] **T083** Add auto-verify task injection to orchestrator: after a code_generate task completes successfully, automatically create and queue a paired type:verify task targeting the files in the task manifest. The verify task inherits the same priority, is inserted next in queue, and is routed to TesterAgent. Add auto_verify: true/false to orchid.defaults.yaml (default false, opt-in per project via .orchid.yaml). `type:code_generate` `p2`
- [x] **T077** Update README.md and docs/getting-started.md for new features: --run-task flag, SKIP task status ([~]), Active/Inactive project grouping, Project Config tab, Planning tab Discussion history, orchid serve --bots/--telegram/--slack flags (already partially documented but needs the new UI features added) `type:draft` `p2`
- [x] **T078** Update CLAUDE.md hot memory: reflect current state — 446+ tests passing, V2.1 complete, new CLI flags (--run-task, task skip), active/inactive projects in .orchid.yaml, SKIP task status syntax [~] in tasks.md, Discussion history tab in Planning UI `type:draft` `p2`
- [x] **T076** Planning tab Discussion tab: added Discussion tab to ArtifactPanel alongside artifact tabs. Loads from existing GET /api/projects/{id}/discussion endpoint, renders chat-style bubbles (same CSS as DiscussionPanel) in a scrollable read-only view. `type:code_generate` `p2`
- [x] **T070** Add Project Settings panel to Web UI: 'Config' tab shows read-only .orchid.yaml and .env (sensitive values redacted). GET /api/projects/{id}/settings endpoint. `type:code_generate` `p2`
- [x] **T071** Add Active/Inactive project grouping to Web UI Projects panel: expandable Active/Inactive folders with ⏸/▶ toggle. Stored in .orchid.yaml active:true/false. Telegram and Slack bots filter to active-only projects. `type:code_generate` `p2`
- [x] **T064** Fix --log-level flag: convert input to lowercase before passing to uvicorn. uvicorn expects lowercase log levels (debug, info, warning) but users may type DEBUG, INFO etc. Add .lower() to the log_level parameter before passing to uvicorn.run() `type:code_generate` `p2`
- [x] **T065** test task from central slack bot `type:draft` `p2`
- [x] **T066** Update README.md to document V2.1 central bot architecture: orchid serve --bots/--telegram/--slack flags, deprecation of orchid telegram and orchid slack commands, Telegram underscore commands (/orchid_projects, /orchid_switch etc), Slack hyphen commands (/orchid-status, /orchid-projects etc), channel routing, slack-channels.json and telegram-state.json state files `type:draft` `p2`
- [x] **T067** Update CLAUDE.md to reflect current project state: 446 tests passing, V2.1 complete, central bot architecture, update Not yet built section, update CLI reference with all new commands `type:draft` `p2`
- [x] **T068** Update orchid-serve.service.template to include --bots flag and document TELEGRAM_BOT_TOKEN and SLACK_BOT_TOKEN environment variables needed `type:draft` `p2`
- [x] **T063** Add /orchid-unlink-channel Slack command: removes the current channel from slack-channels.json so it can be relinked to a different project. `type:code_generate` `p2`
- [x] **T061** Fix Slack auto-channel creation: added _ensure_channels_for_all_projects() called in start() to create channels for projects that existed before bot startup. `type:code_generate` `p2`
- [x] **T060** Add agent instruction to CLAUDE.md and system prompt: when asked to ADD content to an existing file (README, docs, etc) use append_file not write_file. Only use write_file when the task explicitly says to replace/rewrite the entire file. `type:code_generate` `p2`
- [x] **T055** Fix local KV cache hit detection: change absolute tok/ms threshold to relative ms/tok threshold (<1.0ms per token = cache hit). Add rolling average tracking for better calibration per model. `type:code_generate` `p2`
- [x] **T054** Fix test_duckduckgo_backend_returns_results in tests/test_search.py — DDG HTML scraping is unreliable in CI/automated environments. Mark test with @pytest.mark.skip(reason='DDG scraping unreliable in automated environments') or make it conditional on a ORCHID_NETWORK_TESTS=true env var. `type:code_generate` `p2`
- [x] **T043** Add auto-review config to orchid.defaults.yaml: when auto_review.enabled is true, after every N code_generate tasks automatically insert a review task that runs check_imports and syntax verification on all files written in the previous N tasks. Default: auto_review.enabled=false, auto_review.after_n_tasks=3 `type:code_generate` `p2`
- [x] **T044** Add project_context() tool that reads package.json (JS projects) or pyproject.toml/setup.py (Python projects) and extracts: module system (esm/commonjs), main framework, language, test framework. Inject this into agent context at task start so agents automatically use correct import syntax for the project. `type:code_generate` `p2`
- [x] **T045** Add file manifest to task completion: when an agent marks a task done append files_created and files_modified lists to the task result in session log. Subsequent tasks can query this manifest via a new tool get_task_files(task_id) to know exact filenames created by previous tasks rather than guessing. `type:code_generate` `p2`
- [x] **T039** Add --model flag to --add-task CLI command so users can specify model:claude|local|auto without embedding it in the task title string `type:code_generate` `p2`
- [x] **T037** Create scripts/deploy.sh — one-command deploy script that: 1) builds React frontend (npm run build in orchid/interfaces/web_ui/), 2) reinstalls orchid globally (uv tool install . --force), 3) restarts orchid-serve systemd service (sudo systemctl restart orchid-serve), 4) tails logs for 5 seconds to confirm clean startup. Add usage instructions as comments at top of script. `type:code_generate` `p2`
- [x] **T034** Fix orchid task done subcommand - should not require TITLE argument when --id is provided `type:code_generate` `p2`
- [x] **T030** Test CLI --help option `type:draft` `p2`
- [x] **T027** test task from Slack `type:draft` `p2`
- [x] **T028** Fix Slack formatter: hot memory code blocks missing closing triple backtick in Slack messages `type:draft` `p2`
- [x] **T019** Add task archiving - completed tasks older than N days move to tasks.md archive section to keep board clean `type:code_generate` `p2`
- [x] **T020** Add orchid telegram systemd service install script to scripts/ `type:code_generate` `p2`
- [x] **T016** test task from Telegram `type:draft` `p2`
- [x] **T015** test task from Telegram `type:draft` `p2`
- [x] **T013** Fix CLAUDE.md compression truncating decision entries `type:code_generate` `p2`
- [x] **T009** Fix orchid task add subcommand - unexpected extra argument error `type:code_generate` `p2`
- [x] **T003** Preserve prior summary on re-compression `type:code_generate` `p2`
- [x] **T004** Add multi-cycle compression tests `type:code_generate` `p2`
- [x] **T005** Document _save() contract in docstring `type:draft` `p3`
- [x] **T006** Wire context window size to orchid.defaults.yaml `type:code_generate` `p3`
