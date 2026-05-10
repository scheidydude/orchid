# Summary of Changes — Orchid V2.4

_Session date: 2026-05-10. Branch: main. Head commit: b7ededd._

---

## 1. Changes Delivered

### Fix: Suspend/Resume with Subprocess Pool Mode (`fix(phase4)` — d201301)

**Problem:** With `isolation.subprocess_enabled: true` (the default since Phase 3), tasks run in child processes. The parent's `agent_registry` had no entry for them, so `/suspend` and `/resume` always returned 404.

**Fix:** SIGSTOP/SIGCONT sent to the pool worker process via OS signals.

**Files changed:**
- `orchid/subprocess_runner.py` — `_PoolWorker` tracks `_current_task_id` + `_is_suspended`; `suspend()` → SIGSTOP, `resume()` → SIGCONT; `WorkerPool._find_worker_for_task()` + 4 new pool methods; 4 module-level helpers (`pool_suspend_task`, `pool_resume_task`, `pool_is_suspended`, `pool_is_running`)
- `orchid/runner.py` — `suspend_task()`/`resume_task()`/`is_suspended()` fall back to pool when `agent_registry` misses
- `orchid/interfaces/web_server.py` — suspend/resume/status endpoints route through `runner` instead of `agent_registry` directly

**Tradeoff:** SIGSTOP freezes mid-instruction, not at a ReAct iteration boundary. No checkpoint saved on SIGSTOP. Acceptable: subprocess isolation means the parent has no agent state to checkpoint anyway.

---

### Feature: LLM Provider Fallback Chain (5 commits — da4b818..6c07360)

**Problem:** A single transient 503/429 from the primary provider immediately marks the task BLOCKED, even when equivalent alternatives are configured.

**Fix:** Ordered fallback list in config. Orchestrator tries providers in sequence on retriable errors before marking BLOCKED.

**Configuration:**
```yaml
# .orchid.yaml
providers:
  task_types:
    code_generate:
      name: claude
      fallback: [openrouter, local]

# orchid.defaults.yaml (already set)
providers:
  fallback_on_errors: [429, 503, 502]
  max_fallback_attempts: 3
```

**Files changed:**
| File | Change |
|------|--------|
| `orchid/providers/base.py` | Added `RetriableProviderError` — explicit signal for transient failures |
| `orchid/providers/registry.py` | Added `resolve_chain()` — returns `(primary, [fallbacks])` from config |
| `orchid/tools/models.py` | Added `fallback: list[str] | None` field to `RouteDecision` |
| `orchid/orchestrator.py` | `_resolve_provider()` uses `resolve_chain()`; `_execute_task()` loops over `[primary] + fallbacks`; `_is_retriable_exc()` helper |
| `orchid/cost/scheduler.py` | Added `record_retriable_error(provider, status_code)` — marks provider rate-pressured |
| `orchid/orchid.defaults.yaml` | Added `providers.fallback_on_errors` and `providers.max_fallback_attempts` |

---

### Fix: Code Review Corrections (`fix(review)` — b7ededd)

| Finding | Severity | Fix |
|---------|----------|-----|
| `_is_retriable_exc` substring match on status codes — "5029" matched "502" | CRITICAL | Word-boundary regex `\b502\b` |
| `record_retriable_error` int vs str comparison — int 429 skipped scheduler update | MEDIUM | `int(status_code) == 429` |
| `RouteDecision.fallback` type annotation used `# type: ignore` workaround | LOW | `list[str] \| None = None` |

---

## 2. Issues Found and Remaining

| ID | Severity | Location | Issue |
|----|----------|----------|-------|
| R1 | LOW | `subprocess_runner.py:173` | SIGSTOP on already-dead process: OSError caught silently; `_is_suspended=True` set even if process terminated. Benign but misleading. |
| R2 | LOW | `subprocess_runner.py` | `pool_is_running()` is defined but not called anywhere in the codebase. Dead code. |
| R3 | MEDIUM | `orchestrator.py:403` | Offline-mode `RouteDecision` drops fallback chain (fallback populated at 399, new object at 403 without it). Intentional but silent; add comment. |
| R4 | LOW | `subprocess_runner.py` | `suspend()`/`resume()` read `_is_suspended` without `self._lock`. Benign TOCTOU — signal is atomic, but flag could be stale. |
| R5 | LOW | `providers/registry.py` | `resolve_chain()` silently drops fallback entries that aren't in `self._providers`. No warning logged when valid-looking names are dropped. |
| R6 | PRE-EXISTING | `tests/test_providers.py` | 6 `resolve_name` tests fail — pre-existing, unrelated to this session's changes. `patch("orchid.config.get", return_value={})` interacts unexpectedly. |
| R7 | PRE-EXISTING | `tests/test_integration.py`, `tests/test_metrics.py` | 2 tests hit live Claude API — result text varies, assertions fail intermittently. Not unit tests; need mocking. |
| R8 | PRE-EXISTING | `orchestrator.py:455` | `'Task' object has no attribute 'to_dict'` — checkpoint capture fails silently every task. Logged as warning; not blocking. |

---

## 3. Suggestions

1. **Fix R1** — check `self._proc.poll() is not None` before sending SIGSTOP; skip and log if process exited.
2. **Fix R2** — remove `pool_is_running()` or wire it to the `/run/status` endpoint's `running` field.
3. **Fix R3** — add `# offline mode: no fallback` comment at line 403 so intent is clear.
4. **Fix R5** — add `logger.debug("resolve_chain: dropping unknown fallback provider %r", name)` when filtering.
5. **Fix R8** — `Task.to_dict()` is missing. Either add the method or change the checkpoint call to use `task.__dict__` or `dataclasses.asdict(task)`.
6. **Mock live-Claude tests** — `test_integration.py` and `test_metrics.py` should use `unittest.mock.patch` on `orchid.tools.models.call` to return deterministic fixture responses.
7. **Subprocess fallback CPU accounting** — `_last_subprocess_cpu_s` only captures the last attempt; sum across all fallback attempts for accurate CPU budget enforcement.

---

## 4. Next Steps (Priority Order)

| Priority | Feature | Effort | From plan |
|----------|---------|--------|-----------|
| 1 | Agent capability versioning (`checkpoint/schema.py`, `capability.py`) | XS (1 day) | `docs/next-features-plan.md` #5 |
| 2 | OpenTelemetry observability (`telemetry.py`, `orchestrator.py`, `agents/base.py`) | S (2–3 days) | `docs/next-features-plan.md` #4 |
| 3 | Fix pre-existing test failures (R6, R7, R8) | S (1 day) | This review |
| 4 | Distributed task queue / Redis Streams | M (1 week) | `docs/next-features-plan.md` #3 |
| 5 | Async agent execution | L (2–3 weeks) | `docs/next-features-plan.md` #2 |
| 6 | Network namespace isolation | M (1 week) | `docs/next-features-plan.md` #6 |

Before starting #1, confirm: has the worker pool been tested with a real task end-to-end with `isolation.subprocess_enabled: true`?
