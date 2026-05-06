# Phase 7 — Formal Resource / Cost Scheduling

**Requires Phase 4 (parallelism) for provider dispatch. Phase 6 (pool) optional.**
**Deploy after phase: Yes** — cost tracking is passive by default (`cost.enforce_budget: false`). Routing changes only activate when `cost.prefer_local_under_pressure: true`.
**Pre-deploy check:** `pytest tests/test_cost_ledger.py tests/test_cost_scheduler.py` pass.

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

- [ ] **T207** Review cost scheduling implementation `type:code_review` `p1` `needs:T205,T206,T204`

Review files: `orchid/cost/ledger.py`, `orchid/cost/scheduler.py`, `orchid/orchestrator.py` (T203, T203b, and T204 additions only).

Check for exactly these issues:
1. **Ledger lock scope** — does `daily_spend()` hold `_lock` while iterating lines? If a write (`record()`) arrives mid-read, could the reader see a partial line? Report PASS if read is done inside `with self._lock:`.
2. **`_rate_flags` global state in tests** — `_rate_flags` is module-level. If tests run in parallel, one test's `set_rate_pressure` could affect another. Report PASS if test file clears `_rate_flags` before each test.
3. **Cost override skips offline mode** — does `_resolve_provider` skip the cost override when `self.offline_mode` is True? Report PASS or FAIL with line number.
4. **Token extraction from agent history** — `getattr(m, "input_tokens", 0)` assumes `Message` objects have `input_tokens` attribute. Does `orchid.tools.models.Message` have this field? If not, the sum will always be 0. Report FAIL if field is absent.
5. **`set_rate_pressure` import path** — in orchestrator.py, is `set_rate_pressure` imported as a module-level function (not method)? Report PASS or FAIL.

---

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
