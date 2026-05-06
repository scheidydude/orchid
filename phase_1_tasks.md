# Phase 1 — Security Hardening

**Deploy after phase: Yes** — all changes backward-compatible. No API surface changes.
**Pre-deploy check:** `pytest tests/test_circuit_breaker.py tests/test_hook_audit.py tests/test_agent_permissions.py` all pass.

---

- [ ] **T151** Create `orchid/hooks/circuit_breaker.py` `type:code_generate` `p1` `model:local`

Create new file `orchid/hooks/circuit_breaker.py`. Define exactly two classes, nothing else.

**`CircuitOpenError(Exception)`**: body is `pass`.

**`CircuitBreaker`**:
Constructor: `__init__(self, name: str, failure_threshold: int = 5, recovery_window_s: float = 60.0, open_duration_s: float = 120.0)`.
Private fields: `self._name = name`, `self._threshold = failure_threshold`, `self._window = recovery_window_s`, `self._open_dur = open_duration_s`, `self._failures: list[float] = []` (monotonic timestamps), `self._opened_at: float | None = None`, `self._lock = threading.Lock()`.

**`state` property** returns `str`. Logic (all times via `time.monotonic()`): if `_opened_at` is None → `"CLOSED"`. If `time.monotonic() - _opened_at < _open_dur` → `"OPEN"`. Else → `"HALF_OPEN"`.

**`call(self, fn: Callable[[], Any]) -> Any`** — thread-safe, acquires `_lock`.
- state `"OPEN"` → raise `CircuitOpenError(f"Circuit '{self._name}' is open")`.
- state `"HALF_OPEN"` → run fn without holding lock (release before call, re-acquire after). On success: clear `_opened_at = None`, clear `_failures = []`. On any exception: set `_opened_at = time.monotonic()`, re-raise.
- state `"CLOSED"` → run fn without holding lock. On success: return value. On any exception: call `self._record_failure()` then re-raise.

**`_record_failure(self) -> None`** — not thread-safe (caller holds lock). Append `time.monotonic()` to `_failures`. Prune entries where `time.monotonic() - entry > _window`. If `len(_failures) >= _threshold`: set `_opened_at = time.monotonic()`, clear `_failures = []`.

**`reset(self) -> None`** — acquire lock, set `_failures = []`, set `_opened_at = None`.

Imports at top: `from __future__ import annotations`, `import threading`, `import time`, `from collections.abc import Callable`, `from typing import Any`. No other imports.

---

- [ ] **T152** Wire circuit breaker into HTTP hook handler in `orchid/hooks/loader.py` `type:code_generate` `p1` `needs:T151` `model:local`

Read `orchid/hooks/loader.py` first.

Make exactly two changes:

**Change 1** — in `HookLoader.__init__`, add after `self._section_counts: dict[str, int] = {}`:
```python
self._circuit_breakers: dict[str, CircuitBreaker] = {}
```
Add import at top of file: `from orchid.hooks.circuit_breaker import CircuitBreaker, CircuitOpenError`.

**Change 2** — in `_create_http_handler(self, hook: HTTPHook) -> callable`, replace the inner `try:` block (the one that calls `requests.request(...)`) with:
```python
try:
    breaker = self._circuit_breakers.setdefault(
        hook.name, CircuitBreaker(name=hook.name)
    )
    def _do_request():
        import requests
        return requests.request(
            method=hook.method,
            url=url,
            headers=hook.headers,
            data=payload,
            timeout=hook.timeout,
        )
    response = breaker.call(_do_request)
    return {
        "status_code": response.status_code,
        "response": response.text[:500],
    }
except CircuitOpenError as e:
    logger.warning("HTTP hook %s skipped: %s", hook.name, e)
    return {"error": "circuit open", "circuit_open": True}
except Exception as e:
    logger.error("HTTP hook %s error: %s", hook.name, e)
    return {"error": str(e)}
```
Remove the existing `import requests` line inside the handler (it moves into `_do_request`). Keep `url` and `payload` substitution lines unchanged above the try block.

---

- [ ] **T153** Create `orchid/hooks/audit.py` `type:code_generate` `p1` `model:local`

Create new file `orchid/hooks/audit.py`. Define exactly one class.

**`ShellHookAuditor`**:
Constructor: `__init__(self, project_dir: Path)`. Stores `self._audit_file = Path(project_dir) / ".orchid" / "hook_audit.jsonl"`.

**`log(self, hook_name: str, event_type: str, command: str, exit_code: int, stdout: str, stderr: str, duration_ms: float) -> None`**:
- Creates parent directory if missing: `self._audit_file.parent.mkdir(parents=True, exist_ok=True)`.
- Builds dict: `{"ts": datetime.now(UTC).isoformat(), "hook": hook_name, "event": event_type, "cmd": command[:200], "exit_code": exit_code, "stdout": stdout[:500], "stderr": stderr[:500], "duration_ms": round(duration_ms, 1)}`.
- Appends JSON line to file (open with `"a"`, write `json.dumps(record) + "\n"`).
- Wraps file write in try/except, logs warning on failure (never raises).

Imports: `from __future__ import annotations`, `import json`, `import logging`, `from datetime import UTC, datetime`, `from pathlib import Path`. No other imports.
`logger = logging.getLogger(__name__)`.

---

- [ ] **T154** Wire audit logging into shell hook handler in `orchid/hooks/loader.py` `type:code_generate` `p1` `needs:T152,T153` `model:local`

Read `orchid/hooks/loader.py` first (it now contains circuit breaker changes from T152).

Make exactly two changes:

**Change 1** — in `HookLoader.__init__`, add after the `self._circuit_breakers` line:
```python
from orchid.hooks.audit import ShellHookAuditor
self._auditor = ShellHookAuditor(self.project_dir)
```

**Change 2** — in `_create_shell_handler(self, hook: ShellHook) -> callable`, inside the `handler` closure, wrap the `subprocess.run(...)` call with timing and audit. Before the `result = subprocess.run(...)` line, add `import time as _time; _t0 = _time.monotonic()`. After the `subprocess.run(...)` line (before the `if result.returncode != 0:` check), add:
```python
_duration_ms = (_time.monotonic() - _t0) * 1000
self._auditor.log(
    hook_name=hook.name,
    event_type=event.event_type,
    command=command[:200],
    exit_code=result.returncode,
    stdout=result.stdout[:500],
    stderr=result.stderr[:500],
    duration_ms=_duration_ms,
)
```
Note: `self` inside the closure refers to the `HookLoader` instance. The closure captures `self` from `_create_shell_handler`. Verify this is already the pattern in the file before writing.

---

- [ ] **T155** Add `allowed_tools` filtering to `BaseAgent` in `orchid/agents/base.py` `type:code_generate` `p1` `model:local`

Read `orchid/agents/base.py` first.

Make exactly two changes to the `BaseAgent` class:

**Change 1** — add class variable after `_require_file_write: bool = False`:
```python
allowed_tools: frozenset[str] | None = None
```

**Change 2** — at the end of `BaseAgent.__init__`, after `self.injection_queue_path = ...`, add:
```python
# Apply tool capability restrictions
_config_tools = cfg.get(f"agents.{self.agent_type}.allowed_tools", None)
if _config_tools:
    _allowed = frozenset(_config_tools)
elif self.allowed_tools is not None:
    _allowed = self.allowed_tools
else:
    _allowed = None
if _allowed is not None:
    _removed = [k for k in list(self.tools) if k not in _allowed]
    for _k in _removed:
        del self.tools[_k]
    if _removed:
        logger.debug(
            "[%s] allowed_tools restricted; removed: %s",
            self.__class__.__name__, _removed,
        )
```

No other changes. `cfg` is already imported in this file.

---

- [ ] **T156** Set `allowed_tools` on TesterAgent, ReviewerAgent, ResearcherAgent `type:code_generate` `p1` `needs:T155` `model:local`

Read `orchid/agents/tester.py`, `orchid/agents/reviewer.py`, `orchid/agents/researcher.py` one by one.

In **`TesterAgent`** (tester.py): add class variable after `_require_file_write: bool = False` (or after `agent_name` if `_require_file_write` is not present):
```python
allowed_tools: frozenset[str] | None = frozenset({
    "read_file", "list_dir", "bash", "check_imports", "get_task_files",
})
```

In **`ReviewerAgent`** (reviewer.py): add class variable after `agent_name`:
```python
allowed_tools: frozenset[str] | None = frozenset({
    "read_file", "list_dir", "bash", "check_imports", "get_task_files",
})
```

In **`ResearcherAgent`** (researcher.py): add class variable after `agent_name`:
```python
allowed_tools: frozenset[str] | None = frozenset({
    "read_file", "list_dir", "bash", "search", "fetch", "get_task_files",
})
```

`DeveloperAgent` gets no `allowed_tools` (keeps all tools). Do not modify `developer.py`.

---

- [ ] **T157** Add permissions and circuit-breaker config to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `needs:T155` `model:local`

Read `orchid/orchid.defaults.yaml` first. Append the following block at the very end of the file (after the `mcp_servers: {}` line):

```yaml

# Agent tool permissions — list tool names to restrict an agent to only those tools.
# Empty list (default) means: use the class-level allowed_tools default.
# Non-empty list overrides the class default entirely.
agents:
  developer:
    allowed_tools: []   # unrestricted by default
  tester:
    allowed_tools: []   # uses class default: read_file, list_dir, bash, check_imports, get_task_files
  reviewer:
    allowed_tools: []   # uses class default: read_file, list_dir, bash, check_imports, get_task_files
  researcher:
    allowed_tools: []   # uses class default: read_file, list_dir, bash, search, fetch, get_task_files

# Circuit breaker defaults for HTTP hooks.
# Failure threshold and timing documented here; currently hard-coded in CircuitBreaker defaults.
# failure_threshold: 5 failures within recovery_window_s → circuit opens for open_duration_s.
hooks:
  circuit_breaker:
    failure_threshold: 5
    recovery_window_s: 60
    open_duration_s: 120
```

Do not modify any other part of the file.

---

- [ ] **T158** Create `tests/test_circuit_breaker.py` `type:code_generate` `p1` `needs:T151` `model:local`

Create file `tests/test_circuit_breaker.py`. Write exactly 5 test functions, no fixtures needed.

```python
from orchid.hooks.circuit_breaker import CircuitBreaker, CircuitOpenError
import time
```

**`test_circuit_starts_closed`**: create `CircuitBreaker("test")`, assert `cb.state == "CLOSED"`, call `cb.call(lambda: 42)` returns `42`.

**`test_circuit_opens_after_threshold`**: create `CircuitBreaker("t", failure_threshold=3, recovery_window_s=60, open_duration_s=120)`. Call `cb.call(fn)` where `fn` raises `ValueError` — catch the `ValueError`. Repeat 3 times total. Assert `cb.state == "OPEN"`. Assert next `cb.call(lambda: 1)` raises `CircuitOpenError`.

**`test_closed_circuit_passes_through_on_success`**: create `CircuitBreaker("t", failure_threshold=5)`. Call with function that increments a counter list `[0]` and returns `"ok"`. Assert return value is `"ok"` and counter is `1`.

**`test_circuit_open_does_not_call_fn`**: create breaker, open it by triggering 5 failures. Create a counter. Call `cb.call` inside try/except `CircuitOpenError`. Assert counter is still `0` (fn was never called).

**`test_reset_closes_circuit`**: open circuit via 5 failures. Assert state `"OPEN"`. Call `cb.reset()`. Assert state `"CLOSED"`. Assert `cb.call(lambda: True)` returns `True`.

---

- [ ] **T159** Create `tests/test_hook_audit.py` `type:code_generate` `p1` `needs:T153` `model:local`

Create file `tests/test_hook_audit.py`. Write exactly 3 test functions using `tmp_path` pytest fixture.

```python
import json
from orchid.hooks.audit import ShellHookAuditor
```

**`test_audit_creates_file(tmp_path)`**: create `ShellHookAuditor(tmp_path)`. Call `log("myhook", "task_start", "echo hi", 0, "hi", "", 12.3)`. Assert `(tmp_path / ".orchid" / "hook_audit.jsonl").exists()`.

**`test_audit_jsonl_format(tmp_path)`**: log one entry. Read file, parse as JSON. Assert keys present: `ts`, `hook`, `event`, `cmd`, `exit_code`, `stdout`, `stderr`, `duration_ms`. Assert `record["hook"] == "myhook"`, `record["exit_code"] == 0`, `record["duration_ms"] == 12.3`.

**`test_audit_appends_multiple(tmp_path)`**: log 3 entries with different hook names. Read file, split lines (strip empties). Assert `len(lines) == 3`. Parse each line as JSON, assert hook names match what was logged.

---

- [ ] **T160** Create `tests/test_agent_permissions.py` `type:code_generate` `p1` `needs:T155,T156` `model:local`

Create file `tests/test_agent_permissions.py`. Write exactly 4 test functions.

```python
from unittest.mock import patch
from orchid.agents.developer import DeveloperAgent
from orchid.agents.tester import TesterAgent
from orchid.agents.reviewer import ReviewerAgent
```

All agents need `project_dir=None` to avoid filesystem calls in `__init__`. Where `cfg.get` is called for `agents.<type>.allowed_tools`, patch it to return `[]` (empty list = use class default).

**`test_developer_has_write_and_delegate`**: with `patch("orchid.agents.base.cfg.get", return_value=None)`: create `DeveloperAgent()`. Assert `"write_file" in agent.tools` and `"bash" in agent.tools`.

**`test_tester_cannot_write`**: with `patch("orchid.agents.base.cfg.get", return_value=None)`: create `TesterAgent()`. Assert `"write_file" not in agent.tools`. Assert `"read_file" in agent.tools` and `"bash" in agent.tools`.

**`test_reviewer_cannot_write`**: with `patch("orchid.agents.base.cfg.get", return_value=None)`: create `ReviewerAgent()`. Assert `"write_file" not in agent.tools`. Assert `"read_file" in agent.tools`.

**`test_yaml_override_restricts_tools`**: mock `cfg.get` to return `["read_file"]` when called with key containing `"allowed_tools"`, else return default. Create `TesterAgent()`. Assert `set(agent.tools.keys()) == {"read_file"}`.

Hint for `test_yaml_override_restricts_tools`: use `side_effect` on the patch that checks the first argument.

---

- [ ] **T161** Review Phase 1 implementation `type:code_review` `p1` `needs:T158,T159,T160,T157`

Review files: `orchid/hooks/circuit_breaker.py`, `orchid/hooks/audit.py`, `orchid/hooks/loader.py` (circuit breaker and audit wiring), `orchid/agents/base.py` (allowed_tools filter).

Check for exactly these issues:
1. **CircuitBreaker thread safety** — does `call()` release the lock before invoking fn? (holding lock during fn call would block all other calls for the duration)
2. **CircuitBreaker HALF_OPEN probe** — if probe succeeds, does it correctly clear `_opened_at` and not leave stale failure timestamps?
3. **Audit log exception safety** — does `ShellHookAuditor.log()` never raise even if the file write fails?
4. **allowed_tools config key** — does the config lookup use the correct agent_type string (matching each agent class's `agent_type` class variable)?
5. **ResearcherAgent search/fetch tools** — `search` and `fetch` are registered in `ResearcherAgent.__init__` after `super().__init__()`. Since the allowed_tools filter runs at end of `BaseAgent.__init__` (before subclass `__init__` finishes), will `search` and `fetch` be present in `self.tools` when the filter runs?

For issue 5: if filter runs before `register_tool("search", ...)`, those tools will be missing from `allowed_tools` check and filtering logic won't find them to remove — but they'll be added afterward unchecked. Report whether this is a problem and suggest fix if needed.

Report each issue as PASS or FAIL with the file and line number.

---

- [ ] **T162** Fix issues found in T161 `type:code_generate` `p1` `needs:T161` `model:local`

Read the T161 review result from `.orchid/task_results.json` or session log. Read each file flagged with FAIL. Apply minimal fixes only — do not refactor anything not flagged. If issue 5 (ResearcherAgent filter timing) is a FAIL, fix by moving the allowed_tools filter into a new `_apply_tool_restrictions()` method called at the end of each agent's `__init__` instead of in `BaseAgent.__init__`. Only make this change if T161 flagged it as FAIL.
