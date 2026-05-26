# Tasks


## DONE

- [x] **T285** Create `orchid/cron/__init__.py` and `orchid/cron/types.py`. `type:code_generate` `p1` `model:local` `output:str = ""`
  - - `orchid/cron/__init__.py` content: exactly one line → `"""Cron-based scheduled task manager for Orchid (D0061)."""`
- `orchid/cron/types.py` imports: `from __future__ import annotations`, `from dataclasses import dataclass, field`, `from datetime import UTC, datetime`, `import uuid`
- Module-level helpers (not exported, used as `default_factory`):
- `def _new_task_id() -> str: return f"stask_{uuid.uuid4().hex[:8]}"`
- `def _new_run_id() -> str: return f"run_{uuid.uuid4().hex[:8]}"`
- `def _utcnow() -> datetime: return datetime.now(UTC)`
- `@dataclass class ScheduledTask:` fields in order:
- `task_id: str = field(default_factory=_new_task_id)` — unique task ID, never changes after creation
- `owner_id: str = ""` — user_id from UserStore
- `name: str = ""` — human-readable display name
- `description: str = ""` — optional longer description
- `enabled: bool = True` — if False, task is not scheduled but kept in store
- `schedule: str = "0 9 * * *"` — cron expression string (five-field standard cron)
- `task_type: str = "agent_prompt"` — must be one of: `"agent_prompt"`, `"mcp_tool"`, `"shell"`
- `config: dict = field(default_factory=dict)` — type-specific execution config (see executor docs)
- `notify_on_failure: bool = True` — log warning + audit event on failure
- `notify_on_success: bool = False` — log audit event on success (always logged regardless)
- `created_at: datetime = field(default_factory=_utcnow)` — UTC creation time
- `last_run_at: datetime | None = None` — UTC time of most recent run completion
- `last_run_status: str | None = None` — status of most recent run: `"success"`, `"failure"`, `"timeout"`, or `None` if never run
- `next_run_at: datetime | None = None` — not computed by dataclass; populated by API layer from APScheduler
- `@dataclass class TaskRun:` fields in order:
- `run_id: str = field(default_factory=_new_run_id)` — unique run ID
- `task_id: str = ""` — references ScheduledTask.task_id
- `owner_id: str = ""` — references User.user_id
- `task_name: str = ""` — snapshot of task name at run time (task may be renamed later)
- `task_type: str = ""` — snapshot of task_type at run time
- `started_at: datetime = field(default_factory=_utcnow)` — UTC start time
- `finished_at: datetime | None = None` — UTC end time; None while running
- `status: str = "running"` — `"running"` | `"success"` | `"failure"` | `"timeout"`
- `output: str = ""` — captured stdout/response (may be empty)
- `error: str = ""` — error message if status is not `"success"` (empty string otherwise)
- Verify: `python3 -c "from orchid.cron.types import ScheduledTask, TaskRun, _new_task_id, _new_run_id; t=ScheduledTask(owner_id='u1', name='test'); r=TaskRun(task_id=t.task_id, owner_id='u1'); assert t.task_id.startswith('stask_'); assert r.run_id.startswith('run_'); print('OK')"` must print `OK`
- [x] **T286** Create `orchid/cron/store.py` (TaskRunStore — append-only JSONL run history with 30-day pruning). `type:code_generate` `p1` `needs:T285` `model:local`
  - - Imports: `import dataclasses, json, logging, threading` from stdlib. `from datetime import UTC, datetime, timedelta`. `from pathlib import Path`. `from orchid.cron.types import TaskRun`
- Module constants: `logger = logging.getLogger(__name__)`. `_TASK_RUN_FIELDS = {f.name for f in dataclasses.fields(TaskRun)}`. `_RETENTION_DAYS = 30`
- `class TaskRunStore:` — manages `~/.config/orchid/cron/runs.jsonl`; one JSON object per line; thread-safe via `self._lock = threading.Lock()`
- `__init__(self, runs_file: Path | None = None) -> None:` — `self._file = runs_file or Path.home() / ".config" / "orchid" / "cron" / "runs.jsonl"`. `self._lock = threading.Lock()`. At end of `__init__`, call `self._prune()`.
- `_prune(self) -> None:` — removes runs older than `_RETENTION_DAYS` days. If `self._file` does not exist, return immediately. Compute `cutoff = datetime.now(UTC) - timedelta(days=_RETENTION_DAYS)`. Read all lines from file. For each line: parse JSON, read `"started_at"` field as ISO string, parse with `datetime.fromisoformat()`, if `tzinfo is None` replace with UTC. Keep line if `started_at >= cutoff` OR if parsing fails. Rewrite file with kept lines only. Catch all exceptions with `logger.warning(...)` — never raises.
- `append(self, run: TaskRun) -> None:` — writes one `TaskRun` as JSON line. Acquire `self._lock`. Create parent dirs (`self._file.parent.mkdir(parents=True, exist_ok=True)`). Serialize with `json.dumps(dataclasses.asdict(run), default=str) + "\n"`. Open file in append mode `"a"`. Catch all exceptions with `logger.error(...)` — never raises.
- `get_runs(self, task_id: str = "", owner_id: str = "", limit: int = 50) -> list[TaskRun]:` — read all lines, filter by `task_id` and `owner_id` if non-empty. Parse datetime fields `"started_at"` and `"finished_at"` with `datetime.fromisoformat()` (set to `None` on parse failure). Filter parsed dict keys to `_TASK_RUN_FIELDS`. Construct `TaskRun(**filtered)`. Sort result newest-first by `started_at` (use `datetime.min.replace(tzinfo=UTC)` as fallback for None). Return first `limit` items. Return `[]` if file doesn't exist. Catch all exceptions with `logger.warning(...)`.
- Verify: `python3 -c "import tempfile, pathlib; from orchid.cron.store import TaskRunStore; from orchid.cron.types import TaskRun; s=TaskRunStore(pathlib.Path(tempfile.mktemp())); r=TaskRun(task_id='t1',owner_id='u1',status='success'); s.append(r); runs=s.get_runs(task_id='t1'); assert len(runs)==1 and runs[0].task_id=='t1'; print('OK')"` must print `OK`
- [x] **T287** Update `orchid/auth/types.py` — add `scheduled_tasks` field to `User`. `type:code_generate` `p1` `model:local`
  - - In `orchid/auth/types.py`, inside `@dataclass class User:`, add one field after the last existing field `token: str = ""`:
- `scheduled_tasks: list[dict] = field(default_factory=list)` — list of raw dicts, each representing one `ScheduledTask`; stored as dicts to avoid circular import with `orchid.cron.types`
- Do NOT change any other field or import. Do NOT add any import for `orchid.cron`.
- Verify: `python3 -c "from orchid.auth.types import User; u=User(user_id='x'); assert isinstance(u.scheduled_tasks, list) and len(u.scheduled_tasks)==0; print('OK')"` must print `OK`
- [x] **T288** Update `orchid/auth/base.py`, `orchid/auth/store.py`, and `orchid/auth/audit.py` to support scheduled tasks. `type:code_generate` `p1` `needs:T287` `model:local`
  - - **`orchid/auth/base.py`**: Add 4 abstract methods to `BaseUserStore` class after `list_oauth_accounts_for_user`:
- `@abstractmethod` `def get_scheduled_task(self, user_id: str, task_id: str) -> dict | None: ...`
- `@abstractmethod` `def upsert_scheduled_task(self, user_id: str, task: dict) -> None: ...`
- `@abstractmethod` `def delete_scheduled_task(self, user_id: str, task_id: str) -> bool: ...`
- `@abstractmethod` `def get_all_enabled_scheduled_tasks(self) -> list[tuple[str, dict]]: ...`
- No new imports needed.
- **`orchid/auth/store.py`**: Add 4 concrete methods to `FileUserStore` class after `list_oauth_accounts_for_user`:
- `get_scheduled_task(self, user_id: str, task_id: str) -> dict | None:` — acquire `self._lock`. Get user from `self._users`. If user is None, return None. Iterate `user.scheduled_tasks`; return `dict(task)` where `task.get("task_id") == task_id`. Return None if not found.
- `upsert_scheduled_task(self, user_id: str, task: dict) -> None:` — acquire `self._lock`. Get user; raise `AuthError(f"User {user_id!r} not found")` if None. Build new list: all existing tasks where `task.get("task_id") != task["task_id"]`, then append `task`. Set `user.scheduled_tasks = new_list`. Call `self._save()`.
- `delete_scheduled_task(self, user_id: str, task_id: str) -> bool:` — acquire `self._lock`. Get user; return False if None. Filter `user.scheduled_tasks` removing item with matching `task_id`. If length unchanged, return False. Call `self._save()`. Return True.
- `get_all_enabled_scheduled_tasks(self) -> list[tuple[str, dict]]:` — acquire `self._lock`. Iterate all users. For each user, iterate `user.scheduled_tasks`. If `task.get("enabled", True)` is True, append `(user.user_id, dict(task))` to result. Return result list.
- No new imports needed (already imports `AuthError`).
- **`orchid/auth/audit.py`**: Add 2 class attributes to `AuditAction` after the last existing attribute `USER_DEACTIVATED = "user_deactivated"`:
- `SCHEDULED_TASK_RUN = "scheduled_task_run"`
- `SCHEDULED_TASK_FAILED = "scheduled_task_failed"`
- No new imports needed.
- Verify: `python3 -c "from orchid.auth.audit import AuditAction; assert AuditAction.SCHEDULED_TASK_RUN=='scheduled_task_run'; assert AuditAction.SCHEDULED_TASK_FAILED=='scheduled_task_failed'; print('audit OK')"` must print `audit OK`
- Verify: `python3 -c "import tempfile, pathlib; from orchid.auth.store import UserStore; from orchid.auth.types import User; s=UserStore(path=pathlib.Path(tempfile.mktemp())); u=User(user_id='u1',username='u1'); s.add_user(u); task={'task_id':'stask_00000001','name':'T','enabled':True}; s.upsert_scheduled_task('u1',task); assert s.get_scheduled_task('u1','stask_00000001')['name']=='T'; assert s.delete_scheduled_task('u1','stask_00000001'); print('store OK')"` must print `store OK`
- [x] **T289** Create `orchid/cron/executor.py` (TaskExecutor — dispatches by task_type). `type:code_generate` `p1` `needs:T285` `model:local`
  - - Imports: `import logging` from stdlib. `from orchid.cron.types import TaskRun, _utcnow`
- `logger = logging.getLogger(__name__)`
- `class TaskExecutionError(Exception): pass` — raised for known, non-retriable config errors
- Module-level function `_run_agent_prompt(config: dict) -> str:` — executes `agent_prompt` type:
- Import inside function body: `from orchid.providers.registry import get_registry`
- Read `prompt = config.get("prompt", "").strip()`. If empty, raise `TaskExecutionError("agent_prompt config missing required field: 'prompt'")`
- Build `system_parts: list[str] = []`. If `config.get("system", "").strip()` is non-empty, append to `system_parts`.
- If `config.get("mcp_servers", [])` is non-empty: import inside try-block `from orchid.mcp.manager import MCPManager`. Create `mgr = MCPManager()`. Call `mgr.discover_servers()`. For each server name in `mcp_servers`: call `mgr.get_adapter(server_name)`. If adapter is None, append `f"  [server '{server_name}' not found in config]"` to `tool_lines`. Else: call `adapter.connect()`, get `adapter.list_tools()`, for each tool append `f"  - {tool.name}: {tool.description}"` to `tool_lines`, call `adapter.disconnect()`. Wrap adapter operations in try/except catching all exceptions, appending `f"  [server '{server_name}' error: {exc}]"`. Wrap entire MCP block in try/except logging to `logger.warning(...)`. If `tool_lines` is non-empty, append `"Available MCP tools (reference only):\n" + "\n".join(tool_lines)` to `system_parts`.
- Set `system = "\n\n".join(system_parts) if system_parts else None`
- Get registry: `registry = get_registry()`. Read `provider_name = config.get("provider", "").strip()`. If non-empty, get provider via `registry.get_by_key(provider_name)`. Else get via `registry.resolve(agent_type="base")`.
- Call `provider.complete([{"role": "user", "content": prompt}], system=system)`. Return the string result.
- Module-level function `_run_mcp_tool(config: dict) -> str:` — executes `mcp_tool` type:
- Import inside: `from orchid.mcp.manager import MCPManager`
- Read `server_name = config.get("server", "").strip()`. If empty, raise `TaskExecutionError("mcp_tool config missing required field: 'server'")`.
- Read `tool_name = config.get("tool", "").strip()`. If empty, raise `TaskExecutionError("mcp_tool config missing required field: 'tool'")`.
- Read `args = config.get("args", {})`. If not `isinstance(args, dict)`, raise `TaskExecutionError("mcp_tool config field 'args' must be a dict")`.
- Create `mgr = MCPManager()`. Call `mgr.discover_servers()`. Get `adapter = mgr.get_adapter(server_name)`. If None, raise `TaskExecutionError(f"MCP server '{server_name}' not found in config")`.
- Call `adapter.connect()`. In a try/finally block: call `result = adapter.call_tool(tool_name, args)`. In finally: call `adapter.disconnect()`.
- Process `result.content`: if it is a `list`, for each item extract `item.get("text", str(item))` if item is dict else `str(item)`, join with `"\n"`. If not list, return `str(result.content)`.
- Module-level function `_run_shell(config: dict) -> str:` — executes `shell` type:
- Import inside: `from orchid.tools.shell import bash`
- Read `command = config.get("command", "").strip()`. If empty, raise `TaskExecutionError("shell config missing required field: 'command'")`.
- Read `timeout = int(config.get("timeout_sec", 60))`.
- Return `bash(command, timeout=timeout, agent_id="cron")`.
- `class TaskExecutor:` — one class attribute `_DISPATCH: dict = {"agent_prompt": _run_agent_prompt, "mcp_tool": _run_mcp_tool, "shell": _run_shell}`
- `def execute(self, task_dict: dict, owner_id: str) -> TaskRun:` — dispatches execution; always returns a TaskRun, never raises:
- Create `run = TaskRun(task_id=task_dict.get("task_id",""), owner_id=owner_id, task_name=task_dict.get("name",""), task_type=task_dict.get("task_type",""), started_at=_utcnow())`
- Read `task_type = task_dict.get("task_type", "")`. Read `config = task_dict.get("config", {})`. Look up `dispatch_fn = self._DISPATCH.get(task_type)`.
- If `dispatch_fn is None`: set `run.finished_at = _utcnow()`, `run.status = "failure"`, `run.error = f"Unknown task_type: {task_type!r}. Must be one of: {sorted(self._DISPATCH)}"`. Return `run`.
- In try block: `output = dispatch_fn(config)`. Set `run.finished_at = _utcnow()`, `run.status = "success"`, `run.output = output or ""`.
- Except `TaskExecutionError as exc`: set `run.finished_at = _utcnow()`, `run.status = "failure"`, `run.error = str(exc)`.
- Except `Exception as exc`: set `run.finished_at = _utcnow()`, `run.status = "failure"`, `run.error = f"{type(exc).__name__}: {exc}"`. Call `logger.exception("Scheduled task %s raised unexpectedly", task_dict.get("task_id"))`.
- Return `run`.
- Verify: `python3 -c "from orchid.cron.executor import TaskExecutor, TaskExecutionError; e=TaskExecutor(); r=e.execute({'task_id':'t1','task_type':'shell','config':{'command':'echo hello'},'name':'test'},'u1'); assert r.status in ('success','failure'); print('executor OK')"` must print `executor OK` (shell command will run; status depends on allowlist config but executor itself must not raise)
- [x] **T290** Create `orchid/cron/engine.py` (CronEngine — APScheduler BackgroundScheduler wrapper). `type:code_generate` `p1` `needs:T289` `model:local`
  - - Imports: `import logging, threading` from stdlib. `from typing import Any`
- `logger = logging.getLogger(__name__)`
- Module-level singleton: `_engine_instance: "CronEngine | None" = None` and `_engine_lock = threading.Lock()`
- `def get_engine() -> "CronEngine":` — if `_engine_instance is None`, acquire `_engine_lock`, check again, create `CronEngine()`, assign to `_engine_instance`. Return `_engine_instance`. Thread-safe double-checked locking.
- `def reset_engine() -> None:` — set `_engine_instance = None`. Used in tests.
- `class CronEngine:` — wraps APScheduler `BackgroundScheduler`
- `__init__(self) -> None:` — `from apscheduler.schedulers.background import BackgroundScheduler`. Set `self._scheduler = BackgroundScheduler(timezone="UTC")`. Set `self._lock = threading.Lock()`. Set `self._run_store: Any | None = None`. Set `self._executor: Any | None = None`.
- `_get_run_store(self):` — lazy-init: if `self._run_store is None`, `from orchid.cron.store import TaskRunStore; self._run_store = TaskRunStore()`. Return `self._run_store`.
- `_get_executor(self):` — lazy-init: if `self._executor is None`, `from orchid.cron.executor import TaskExecutor; self._executor = TaskExecutor()`. Return `self._executor`.
- `start(self) -> None:` — `from orchid.auth.store import get_store`. Call `get_store().get_all_enabled_scheduled_tasks()`. For each `(owner_id, task_dict)` in result, call `self._register_job(owner_id, task_dict)`. If `not self._scheduler.running`, call `self._scheduler.start()`. Log `logger.info("CronEngine started with %d scheduled task(s)", len(result))`.
- `stop(self) -> None:` — if `self._scheduler.running`, call `self._scheduler.shutdown(wait=False)`. Log `logger.info("CronEngine stopped")`.
- `_register_job(self, owner_id: str, task_dict: dict) -> None:` — `from apscheduler.triggers.cron import CronTrigger`. Read `task_id = task_dict.get("task_id", "")`. Read `schedule = task_dict.get("schedule", "0 9 * * *")`. In try block: `trigger = CronTrigger.from_crontab(schedule, timezone="UTC")`. On exception: `logger.error("Invalid cron expression %r for task %s: %s", schedule, task_id, exc)` and return. Call `self._scheduler.add_job(func=self._run_task, trigger=trigger, args=[owner_id, task_dict], id=task_id, replace_existing=True, misfire_grace_time=300)`. Log `logger.debug("Registered cron job: %s schedule=%s owner=%s", task_id, schedule, owner_id)`.
- `_unregister_job(self, task_id: str) -> None:` — call `self._scheduler.remove_job(task_id)` wrapped in try/except (silently ignore `JobLookupError` and all exceptions).
- `add_or_update_task(self, owner_id: str, task_dict: dict) -> None:` — if `task_dict.get("enabled", True)` is True, call `self._register_job(owner_id, task_dict)`. Else call `self._unregister_job(task_dict.get("task_id",""))`. Callable after `start()` to update live schedule.
- `remove_task(self, task_id: str) -> None:` — call `self._unregister_job(task_id)`.
- `run_now(self, owner_id: str, task_dict: dict) -> None:` — start a daemon thread named `f"cron-manual-{task_dict.get('task_id','?')}"` that calls `self._run_task(owner_id, task_dict)`. Non-blocking.
- `_run_task(self, owner_id: str, task_dict: dict) -> None:` — called by APScheduler jobs and `run_now()`. Never raises. Logic:
1. Log `logger.info("Executing scheduled task %s (%s) for user %s", task_dict.get("task_id"), task_dict.get("name"), owner_id)`.
2. In try/except: `run = self._get_executor().execute(task_dict, owner_id)`. On exception: `logger.error(...)` and return.
3. In try/except: `self._get_run_store().append(run)`. On exception: `logger.error(...)`.
4. In try/except: `from orchid.auth.store import get_store`. Build `updated_task = dict(task_dict)`. Set `updated_task["last_run_at"] = run.finished_at.isoformat() if run.finished_at else None`. Set `updated_task["last_run_status"] = run.status`. Call `get_store().upsert_scheduled_task(owner_id, updated_task)`. On exception: `logger.error(...)`.
5. In try/except: `from orchid.auth.audit import AuditAction, AuditStore, make_event`. Create `AuditStore()`. If `run.status == "success"`: `make_event(user_id=owner_id, action=AuditAction.SCHEDULED_TASK_RUN, resource=task_dict.get("task_id",""), result="success")` and `audit.log(event)`. Else: `make_event(user_id=owner_id, action=AuditAction.SCHEDULED_TASK_FAILED, resource=task_dict.get("task_id",""), result="failure", detail=run.error[:500] if run.error else "")` and `audit.log(event)`. Also log `logger.warning("Scheduled task %s failed: %s", task_dict.get("task_id"), run.error)`. On exception: `logger.error(...)`.
- Verify: `python3 -c "from orchid.cron.engine import CronEngine, reset_engine; reset_engine(); e=CronEngine(); e.start(); e.stop(); print('engine OK')"` must print `engine OK`
- [x] **T291** Create `orchid/cron/api.py` (register_routes — installs all `/api/scheduler/*` endpoints on FastAPI app). `type:code_generate` `p1` `needs:T290` `model:local`
  - - Imports at module level: `import dataclasses, logging` from stdlib. `from datetime import UTC, datetime`. `from typing import Any`
- `logger = logging.getLogger(__name__)`
- Single public function: `def register_routes(app: Any) -> None:` — takes the FastAPI `app` instance (typed as `Any` to avoid import-time FastAPI dependency). If imports fail, `logger.warning(...)` and return without raising.
- Inside `register_routes`, do all framework imports inside the function body (not at module level), wrapped in try/except ImportError that calls `logger.warning(...)` and returns:
- `from fastapi import Depends, HTTPException, Request`
- `from orchid.auth.middleware import require_auth`
- `from orchid.auth.store import get_store`
- `from orchid.cron.types import ScheduledTask`
- `from orchid.cron.store import TaskRunStore`
- `from orchid.cron.engine import get_engine`
- Inside `register_routes` (as local variables/nested functions), define:
- `_run_store = TaskRunStore()` — local variable, instantiated once when `register_routes` is called
- `def _task_to_dict(task_dict: dict) -> dict:` — copies `task_dict`, converts any `datetime` values in keys `created_at`, `last_run_at`, `next_run_at` to ISO strings via `.isoformat()`. Returns cleaned dict.
- `def _run_to_dict(run) -> dict:` — calls `dataclasses.asdict(run)`, converts any `datetime` values in keys `started_at`, `finished_at` to ISO strings. Returns dict.
- `def _validate_task_body(body: dict) -> list[str]:` — returns list of error strings (empty = valid). Checks:
- `body.get("name","").strip()` must be non-empty → error: `"'name' is required and must be non-empty"`
- `body.get("task_type","")` must be in `{"agent_prompt","mcp_tool","shell"}` → error: `"'task_type' must be one of: agent_prompt, mcp_tool, shell"`
- `body.get("schedule","").strip()` must be non-empty → error: `"'schedule' is required (cron expression, e.g. '0 9 * * *')"`
- `body.get("config",{})` must be a `dict` → error: `"'config' must be a dict"`
- `def _find_task_for_user(task_id: str, current_user) -> tuple[dict, str]:` — returns `(task_dict, owner_id)` or raises HTTPException. Logic: if `current_user.role == "admin"`: iterate `get_store().list_users()`; for each user iterate `user.scheduled_tasks`; if `t.get("task_id") == task_id`, return `(dict(t), user.user_id)`. Raise `HTTPException(404, "Task not found")` if not found. Else (non-admin): call `get_store().get_scheduled_task(current_user.user_id, task_id)`. If None, raise `HTTPException(404, "Task not found")`. Return `(task, current_user.user_id)`.
- Note: `_task_to_dict`, `_run_to_dict`, `_validate_task_body`, `_find_task_for_user`, and `_run_store` are all defined as local variables/nested functions inside `register_routes` so the endpoint handlers (also nested inside `register_routes`) can close over them.
- **Endpoint `GET /api/scheduler/tasks`** — `async def list_tasks(current_user=Depends(require_auth())):` — if `current_user.role == "admin"`, collect ALL tasks from all users via `[t for user in get_store().list_users() for t in user.scheduled_tasks]`. Else get `get_store().get_user(current_user.user_id)` and use `user.scheduled_tasks if user else []`. Return `{"tasks": [_task_to_dict(t) for t in tasks], "total": len(tasks)}`.
- **Endpoint `POST /api/scheduler/tasks`** (status_code=201) — `async def create_task(request: Request, current_user=Depends(require_auth())):` — body = `await request.json()`. Validate with `_validate_task_body(body)`. If errors, raise `HTTPException(status_code=400, detail={"errors": errors})`. Build `task_dict` from body: copy keys `name`, `description`, `enabled`, `schedule`, `task_type`, `config`, `notify_on_failure`, `notify_on_success` from body (use defaults if missing: `enabled=True`, `description=""`, `config={}`, `notify_on_failure=True`, `notify_on_success=False`). Generate task ID: `import uuid; task_dict["task_id"] = f"stask_{uuid.uuid4().hex[:8]}"` (do NOT use ScheduledTask dataclass to generate the ID). Set `task_dict["owner_id"] = current_user.user_id`. Set `task_dict["created_at"] = datetime.now(UTC).isoformat()`. Set `task_dict["last_run_at"] = None`. Set `task_dict["last_run_status"] = None`. Set `task_dict["next_run_at"] = None`. Call `get_store().upsert_scheduled_task(current_user.user_id, task_dict)`. If `task_dict.get("enabled", True)`, call `get_engine().add_or_update_task(current_user.user_id, task_dict)`. Return `_task_to_dict(task_dict)`.
- **Endpoint `GET /api/scheduler/tasks/{task_id}`** — `async def get_task(task_id: str, current_user=Depends(require_auth())):` — call `task, owner_id = _find_task_for_user(task_id, current_user)`. Return `_task_to_dict(task)`.
- **Endpoint `PUT /api/scheduler/tasks/{task_id}`** — `async def update_task(task_id: str, request: Request, current_user=Depends(require_auth())):` — body = `await request.json()`. Call `existing_task, owner_id = _find_task_for_user(task_id, current_user)`. Validate body with `_validate_task_body(body)`. If errors, raise `HTTPException(400, detail={"errors": errors})`. Build `updated = dict(existing_task)`. For each key in `["name","description","enabled","schedule","task_type","config","notify_on_failure","notify_on_success"]`, if key is present in body, set `updated[key] = body[key]`. Do NOT update `task_id`, `owner_id`, `created_at`, `last_run_at`, `last_run_status`, `next_run_at` from body. Call `get_store().upsert_scheduled_task(owner_id, updated)`. Call `get_engine().add_or_update_task(owner_id, updated)`. Return `_task_to_dict(updated)`.
- **Endpoint `DELETE /api/scheduler/tasks/{task_id}`** — `async def delete_task(task_id: str, current_user=Depends(require_auth())):` — call `_task, owner_id = _find_task_for_user(task_id, current_user)`. Call `get_store().delete_scheduled_task(owner_id, task_id)`. Call `get_engine().remove_task(task_id)`. Return `{"deleted": True, "task_id": task_id}`.
- **Endpoint `POST /api/scheduler/tasks/{task_id}/run`** — `async def run_task_now(task_id: str, current_user=Depends(require_auth())):` — call `task, owner_id = _find_task_for_user(task_id, current_user)`. Call `get_engine().run_now(owner_id, task)`. Return `{"queued": True, "task_id": task_id}`. Note: run is asynchronous (background thread); this endpoint returns immediately.
- **Endpoint `GET /api/scheduler/tasks/{task_id}/runs`** — `async def get_task_runs(task_id: str, limit: int = 50, current_user=Depends(require_auth())):` — call `_task, _owner_id = _find_task_for_user(task_id, current_user)`. Call `runs = _run_store.get_runs(task_id=task_id, limit=limit)`. Return `{"runs": [_run_to_dict(r) for r in runs], "total": len(runs)}`.
- **Endpoint `GET /api/scheduler/runs`** — `async def list_runs(limit: int = 50, current_user=Depends(require_auth())):` — if admin, call `_run_store.get_runs(limit=limit)`. Else call `_run_store.get_runs(owner_id=current_user.user_id, limit=limit)`. Return `{"runs": [_run_to_dict(r) for r in runs], "total": len(runs)}`.
- Verify: `python3 -c "from orchid.cron.api import register_routes; print('import OK')"` must print `import OK`
- [x] **T292** Update `orchid/interfaces/web_server.py` to wire the CronEngine into the server lifecycle and register scheduler API routes. `type:code_generate` `p1` `needs:T291` `model:local`
  - - This task modifies `orchid/interfaces/web_server.py` in exactly 4 places. Do NOT modify any other part of the file.
- **Change 1**: Add module-level variable after the line `_central_bot_manager: Any | None = None` (around line 80):
```python
_cron_engine: Any | None = None
```
- **Change 2**: In the `_lifespan` async context manager, BEFORE the `yield` statement, add the following block after the orphan recovery loop (after the `for _proj_path in _all_paths:` block ends, before `yield`):
```python
# Start cron engine for scheduled tasks (D0061)
global _cron_engine
try:
from orchid.cron.engine import get_engine as _get_cron_engine
_cron_engine = _get_cron_engine()
_cron_engine.start()
logger.info("CronEngine started")
except Exception as _cron_exc:
logger.warning("CronEngine failed to start: %s", _cron_exc)
```
- **Change 3**: In the `_lifespan` async context manager, in the shutdown section AFTER `yield`, add the following block after the `if _central_bot_manager is not None:` block ends:
```python
if _cron_engine is not None:
try:
_cron_engine.stop()
except Exception as exc:
logger.warning("CronEngine stop error: %s", exc)
```
- **Change 4**: In the `create_app` function, after the `app.add_middleware(CORSMiddleware, ...)` call, add:
```python
# Register scheduler API routes (D0061)
try:
from orchid.cron.api import register_routes as _register_cron_routes
_register_cron_routes(app)
logger.debug("Scheduler API routes registered")
except Exception as _cron_api_exc:
logger.warning("Scheduler API routes not registered: %s", _cron_api_exc)
```
- Verify: `python3 -c "from orchid.interfaces.web_server import create_app; print('import OK')"` must print `import OK`
- [x] **T266** Create `orchid/remote/__init__.py` with content `# Remote worker protocol` and `orchid/remote/types.py`. `type:code_generate` `p1` `model:local`
  - - - `orchid/remote/__init__.py` content: exactly `# Remote worker protocol`
- [x] **T267** Create `orchid/remote/worker_server.py`. A FastAPI server that accepts remote task requests and runs them via `SubprocessRunner`. `type:code_generate` `p1` `needs:T266` `model:local`
  - - - Imports: `import json, os, socket` from stdlib. `from fastapi import FastAPI`. `from orchid.worker_protocol import TaskContext, WorkerResult`. `from orchid.subprocess_runner import SubprocessRunner`. `from orchid.remote.types import RemoteTaskRequest, RemoteTaskResponse`
- [x] **T268** Create `orchid/remote/dispatcher.py`. One class: `RemoteDispatcher`. `type:code_generate` `p1` `needs:T267` `model:local`
  - - - Imports: `import json, logging, threading` from stdlib. `import httpx`. `from orchid.worker_protocol import TaskContext, WorkerResult`. `from orchid.remote.types import WorkerNode, RemoteTaskRequest, RemoteTaskResponse`
- [x] **T269** Add `remote` config block to `orchid/orchid.defaults.yaml`. Read the file first. Append at the bottom. `type:code_generate` `p1` `model:local`
  - - - Append exactly:
```yaml
# T269: Remote worker settings
remote:
enabled: false            # true = dispatch task groups to remote worker nodes
nodes: []                 # list of {node_id: str, url: str, capacity: int} dicts
merge_ledger_after_group: true  # pull and merge cost ledger from nodes after each group
```
- [x] **T270** Extend `orchid/runner.py` — use `RemoteDispatcher` when `remote.enabled` is true. Read the file first. Find `_execute_group()` method (around line 245). `type:code_generate` `p1` `needs:T268,T269` `model:local`
  - - - Add import at top: `from orchid.config import cfg`
- [x] **T271** Create `orchid/capability.py`. One dataclass and one registry dict. `type:code_generate` `p1` `model:local`
  - - - Imports: `from dataclasses import dataclass, field`
- [x] **T272** Extend `orchid/agents/base.py` — read capability from `CAPABILITY_REGISTRY` in `__init__` and use to enforce allowed_tools. Read the file first. Find `__init__()`. `type:code_generate` `p1` `needs:T271` `model:local`
  - - - In `__init__()`, AFTER the existing `allowed_tools` logic (the block around line 342-359 that reads `_config_allowed`), add:
```python
# T272: Override allowed_tools from AgentCapability registry if capability is stricter
try:
from orchid.capability import get_capability
_cap = get_capability(self.__class__.__name__.lower().replace("agent", ""))
if _cap.allowed_tools is not None:
if self._effective_allowed_tools is None:
self._effective_allowed_tools = _cap.allowed_tools
else:
# Intersect: capability further restricts what config already restricted
self._effective_allowed_tools = self._effective_allowed_tools & _cap.allowed_tools
if _cap.max_iterations > 0 and self.max_iterations > _cap.max_iterations:
self.max_iterations = _cap.max_iterations
except Exception as _cap_err:
logger.debug("Capability registry lookup failed: %s", _cap_err)
```
- [x] **T273** Extend `orchid/cost/ledger.py` — add `node_id` field to `TokenRecord` and `merge_from_file()` to `CostLedger`. Read the file first. `type:code_generate` `p1` `model:local`
  - - - Add `node_id: str = ""` as the LAST field in `@dataclass class TokenRecord:` (after `user_id` added in T258, or after the last existing field)
- [x] **T274** Extend `orchid/checkpoint/restore.py` — add `export_checkpoint()` function. Read the file first. Add after `list_checkpoints()`. `type:code_generate` `p1` `model:local`
  - - - Add this function:
```python
def export_checkpoint(
checkpoint_id: str,
source_project_dir: "Path",
dest_dir: "Path",
) -> Path:
"""Copy a checkpoint's files to dest_dir for transfer to a remote node.

Returns the path to the exported checkpoint JSON in dest_dir.
Raises FileNotFoundError if the checkpoint does not exist.
"""
import shutil
from orchid.checkpoint.store import CheckpointStore
store = CheckpointStore(source_project_dir)
cp = store.load(checkpoint_id)
if cp is None:
raise FileNotFoundError(f"Checkpoint {checkpoint_id!r} not found in {source_project_dir}")
dest_dir = Path(dest_dir)
dest_dir.mkdir(parents=True, exist_ok=True)
dest_file = dest_dir / f"{checkpoint_id}.json"
# Re-serialize the checkpoint to the destination
import json, dataclasses
dest_file.write_text(json.dumps(dataclasses.asdict(cp)))
return dest_file
```
- [x] **T275** Extend `orchid/remote/dispatcher.py` — add task migration: if a node becomes overloaded mid-dispatch, retry on another node. Read the file first. Modify `dispatch()`. `type:code_generate` `p1` `needs:T268` `model:local`
  - - - Modify `dispatch()` to retry on a different node if the HTTP call fails with `RemoteDispatcherError`:
- [x] **T280** Review Tier 4 implementation (T266-T279). Check: remote protocol types are correct, dispatcher selects nodes correctly, capability registry matches existing agent frozensets, export_checkpoint works with real CheckpointStore. `type:review` `p1` `needs:T276,T277,T278,T279` `model:claude`
  - - - Run `python -c "from orchid.remote.types import WorkerNode, RemoteTaskRequest, RemoteTaskResponse"` — must not error
- [x] **T281** Fix all issues found in T280. Read the T280 result first. Make exactly the fixes listed. `type:code_generate` `p1` `needs:T280` `model:local`
- [x] **T282** Run full test suite and report results. `type:verify` `p1` `needs:T281` `model:claude`
  - - - Run: `source .venv/bin/activate && python -m pytest tests/ -q --ignore=tests/test_agent_pool.py --ignore=tests/test_parallel_runner.py 2>&1 | tail -20`
- [x] **T283** Fix regressions found in T282. `type:code_generate` `p1` `needs:T282` `model:local`
- [x] **T250** Create `orchid/auth/store.py`. One class: `UserStore`. `type:code_generate` `p1` `needs:T249` `model:local`
  - - - - - - - - - - Imports: `import json, logging, threading` from stdlib. `from pathlib import Path`. `from orchid.auth.types import User, AuthError`
- [x] **T251** Create `orchid/auth/middleware.py`. FastAPI dependency for token-based auth. `type:code_generate` `p1` `needs:T250` `model:local`
  - - - - - - - - - - Imports: `from fastapi import Depends, HTTPException, status`. `from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials`. `from orchid.auth.store import UserStore`. `from orchid.auth.types import User, AuthError`
- [x] **T252** Extend `orchid/web/server.py` — add auth endpoints and optional auth guard. Read the file first. Find the FastAPI `app` instance. `type:code_generate` `p1` `needs:T251` `model:local`
  - - - - - - - - - - Add these imports near the top: `from orchid.auth.store import UserStore`. `from orchid.auth.types import User, AuthError`. `from orchid.auth.middleware import get_optional_user`
- [x] **T255** Extend `orchid/subprocess_runner.py` — if `isolation.container_enabled` is true, use `ContainerRunner` instead of bare subprocess. Read the file first. `type:code_generate` `p1` `needs:T254` `model:local`
  - - - - - - - - - - Add import at top: `from orchid.config import cfg`
- [x] **T257** Extend `orchid/tools/filesystem.py` — call `log_file_write()` after every successful `write_file()` and `append_file()`. Read the file first. `type:code_generate` `p1` `needs:T256` `model:local`
  - - - - - - - - - - Add import: `from orchid.hooks.audit import log_file_write as _audit_file_write`
- [x] **T259** Extend `orchid/cost/scheduler.py` — add `check_user_budget()` method. Read the file first. Find `CostScheduler` class. Add after `check_budget()`. `type:code_generate` `p1` `needs:T258` `model:local`
  - - - - - - - - - - Add this method to `CostScheduler`:
```python
def check_user_budget(self, user_id: str, user_budget_usd: float) -> None:
"""Raise BudgetBlockedError if user has exceeded their personal daily budget.

Only enforced if user_budget_usd > 0.
"""
if user_budget_usd <= 0:
return
if self._ledger is None:
return
spent = self._ledger.daily_spend_for_user(user_id)
if spent >= user_budget_usd:
raise BudgetBlockedError(
f"User '{user_id}' has exceeded daily budget "
f"${user_budget_usd:.2f} (spent ${spent:.2f})"
)
```
- [x] **T263** Review Tier 3 implementation (T249-T262). Check: auth layer is importable, UserStore persists correctly, container runner handles unavailable docker gracefully, file write audit fires, user quota enforcement works. `type:review` `p1` `needs:T260,T261,T262` `model:claude`
  - - - - - - - - - - Run `python -c "from orchid.auth.types import User, AuthError; from orchid.auth.store import UserStore; from orchid.auth.middleware import get_current_user"` — must not error
- [x] **T264** Fix all issues found in T263. Read the T263 result first. Make exactly the fixes listed. `type:code_generate` `p1` `needs:T263` `model:local`
- [x] **T258** Extend `orchid/cost/ledger.py` — add `user_id` field to `TokenRecord` and `daily_spend_for_user()` method to `CostLedger`. Read the file first. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - Find `@dataclass class TokenRecord:` (line 32). Add `user_id: str = ""` as the LAST field (with default so existing code constructing `TokenRecord` without it still works).
- [x] **T256** Extend `orchid/hooks/audit.py` — add `log_file_write()` function to `AuditLogger`. Read the file first. Find the `AuditLogger` class. Add the method after the last existing log method. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - Add this method to `AuditLogger`:
```python
def log_file_write(
self,
task_id: str,
path: str,
agent_id: str = "",
bytes_written: int = 0,
operation: str = "write",   # "write" or "append"
) -> None:
"""Log a file write or append operation to the audit log."""
self._write({
"event": "file_write",
"task_id": task_id,
"path": path,
"agent_id": agent_id,
"bytes_written": bytes_written,
"operation": operation,
})
```
- [x] **T254** Create `orchid/container_runner.py`. One class: `ContainerRunner`. Opt-in; skips gracefully if Docker unavailable. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - Imports: `import json, logging, shutil, subprocess, sys` from stdlib. `from pathlib import Path`. `from orchid.worker_protocol import TaskContext, WorkerResult`
- [x] **T253** Extend `orchid/providers/registry.py` — accept per-user API keys that override env/config keys. Read the file first. Find the provider resolution logic. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - Find the main resolution function or class (likely `resolve_provider()` or `ProviderRegistry`). Add a parameter `user_api_keys: dict[str, str] | None = None` to the resolution function/method.
- [x] **T249** Create `orchid/auth/__init__.py` with content `# Orchid auth layer` and `orchid/auth/types.py`. Define 2 things in `types.py`. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - `orchid/auth/__init__.py` content: exactly `# Orchid auth layer`
- [x] **T247** Fix all issues found in T246. Read the T246 result first. Make exactly the fixes listed. `type:code_generate` `p1` `needs:T246` `model:local`
- [x] **T246** Review Tier 2 implementation (T230-T245). Check: file locks are thread-safe, mid-task checkpoint saves/loads correctly, mailbox is thread-safe, shell permission check works, max_iterations hard cap is read correctly. `type:review` `p1` `needs:T242,T243,T244,T245` `model:claude`
  - - - - - - - - - - - - - - Run `python -c "from orchid.locks import FileLockRegistry, get_file_lock_registry"` — must not error
- [x] **T230** Create `orchid/locks.py`. One class: `FileLockRegistry`. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - - - - - Imports: `import threading, logging` from stdlib. `from pathlib import Path` from stdlib. `from collections import defaultdict`
- [x] **T231** Extend `orchid/tools/filesystem.py` — use `FileLockRegistry` in `write_file()` and `append_file()`. Read the file first. `type:code_generate` `p1` `needs:T230` `model:local`
  - - - - - - - - - - - - - - Add import at the top: `from orchid.locks import get_file_lock_registry`
- [x] **T232** Extend `orchid/checkpoint/schema.py` — add `ReActCheckpoint` dataclass. Read the file first. Find the end of the file (after existing dataclasses). `type:code_generate` `p1` `model:local`
  - - - - - - - - - - - - - - Add this dataclass at the end of the file (after existing definitions):
```python
@dataclass
class ReActCheckpoint:
"""Mid-task ReAct loop checkpoint — saved every N iterations."""
task_id: str
iteration: int
conversation_history: list[dict]   # list of {"role": str, "content": str} dicts
partial_result: str = ""
timestamp: str = ""                # ISO 8601 UTC, set by store
```
- [x] **T233** Extend `orchid/checkpoint/store.py` — add `save_react_checkpoint()` and `load_react_checkpoint()` methods. Read the file first. Add after the `prune()` method. `type:code_generate` `p1` `needs:T232` `model:local`
  - - - - - - - - - - - - - - Add `from orchid.checkpoint.schema import ReActCheckpoint` to the imports (check if schema is already imported; if so, add `ReActCheckpoint` to the existing import)
- [x] **T234** Extend `orchid/agents/base.py` — save a ReAct checkpoint every 5 iterations. Read the file first. Find the `run()` method and the `for iteration in range(self.max_iterations):` loop. `type:code_generate` `p1` `needs:T233` `model:local`
  - - - - - - - - - - - - - - At the TOP of `BaseAgent.__init__()`, add: `self._checkpoint_store: Any = None` (use `from typing import Any` if not already imported)
- [x] **T235** Extend `orchid/orchestrator.py` — wire checkpoint_store and task_id into agent before run. Read the file first. Find the block where `agent` is assigned (via `self._get_agent(...)`) and before `agent.run(plan)` is called. `type:code_generate` `p1` `needs:T234` `model:local`
  - - - - - - - - - - - - - - After `agent = self._get_agent(...)` and BEFORE the `if cfg.get("isolation.subprocess_enabled"...)` block, add:
```python
# T235: Wire ReAct checkpoint store and task_id into agent
try:
from orchid.checkpoint.store import CheckpointStore
agent.set_checkpoint_store(CheckpointStore(self.session.project_dir))
agent._current_task_id = task.id
except Exception as _cs_err:
logger.debug("Could not wire checkpoint store into agent: %s", _cs_err)
```
- [x] **T236** Create `orchid/mailbox.py`. One class: `AgentMailbox`. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - - - - - Imports: `import queue, threading, logging` from stdlib. `from dataclasses import dataclass, field`. `from typing import Any`
- [x] **T237** Extend `orchid/agents/base.py` — add `send_message` and `receive_message` tools. Read the file first. Find `_make_project_tools()` method. `type:code_generate` `p1` `needs:T236` `model:local`
  - - - - - - - - - - - - - - Add `from orchid.mailbox import get_mailbox` import at the top of the file
- [x] **T238** Extend `orchid/orchestrator.py` — drop agent mailbox at task end. Read the file first. Find the `finally:` block inside `_execute_task()` (the block that runs after the agent finishes). `type:code_generate` `p1` `needs:T237` `model:local`
  - - - - - - - - - - - - - - In the `finally:` block of `_execute_task()`, add:
```python
# T238: Clean up agent mailbox
try:
from orchid.mailbox import drop_mailbox
if hasattr(agent, "_mailbox_id"):
drop_mailbox(agent._mailbox_id)
except Exception:
pass
```
- [x] **T239** Extend `orchid/tools/shell.py` — add `agent_id` parameter to `bash()`. Read the file first. Find `def bash(command: str, timeout: int | None = None) -> str:` at line 120. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - - - - - Change the signature to: `def bash(command: str, timeout: int | None = None, agent_id: str = "") -> str:`
- [x] **T240** Add `agents.max_iterations` config block to `orchid/orchid.defaults.yaml`. Read the file first. Find the `agents:` section. Add under it. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - - - - - Under the `agents:` key (after existing agent config lines), add:
```yaml
max_iterations:          # per-agent-type hard cap on ReAct iterations (0 = use agents.max_react_iterations)
developer: 0
tester: 0
researcher: 0
reviewer: 0
base: 0
```
- [x] **T241** Extend `orchid/agents/base.py` — read per-agent-type `max_iterations` from config and enforce hard cap. Read the file first. Find `__init__()`. `type:code_generate` `p1` `needs:T240` `model:local`
  - - - - - - - - - - - - - - In `__init__()`, AFTER `self.max_iterations = cfg.get("agents.max_react_iterations", 25)`, add:
```python
# T241: Per-agent-type hard cap from agents.max_iterations config
_agent_type_key = self.__class__.__name__.lower().replace("agent", "")
_hard_cap = cfg.get(f"agents.max_iterations.{_agent_type_key}", 0)
if _hard_cap and _hard_cap > 0:
self.max_iterations = _hard_cap
```
- [x] **T209** Create `orchid/worker_protocol.py`. Define exactly 3 dataclasses using `@dataclass` from `dataclasses`. Import `json`, `field`, `asdict` from `dataclasses`. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - - - - - - `TaskContext(task_id: str, task_description: str, session_context: str, agent_type: str, model_key: str, project_dir: str, injection_queue_path: str)` — all required, no defaults
- [x] **T210** Create `orchid/worker_subprocess.py`. This is the subprocess entry point — run by the parent via `sys.executable -m orchid.worker_subprocess`. `type:code_generate` `p1` `needs:T209` `model:local`
  - - - - - - - - - - - - - - - Imports: `import json, sys, time, logging` from stdlib. `from pathlib import Path`. `from orchid.worker_protocol import TaskContext, WorkerEvent, WorkerResult`
- [x] **T211** Create `orchid/subprocess_runner.py`. One class: `SubprocessRunner`. `type:code_generate` `p1` `needs:T209` `model:local`
  - - - - - - - - - - - - - - - Imports: `import json, logging, subprocess, sys` from stdlib. `from collections.abc import Callable`. `from orchid.worker_protocol import TaskContext, WorkerEvent, WorkerResult`
- [x] **T212** Append isolation config block to `orchid/orchid.defaults.yaml`. Read the file first to find its end. Append exactly this block at the bottom. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - - - - - - Append exactly:
```yaml
# T212: Subprocess isolation settings
isolation:
subprocess_enabled: false   # true = each task runs in a child process
max_task_seconds: 0         # wall-clock timeout per task (0 = no limit)
container_enabled: false    # true = use docker container (Tier 3)
```
- [x] **T213** Extend `orchid/orchestrator.py` — add `_run_task_isolated()` method. Read the file first. Find the method `_resolve_provider` (around line 297). Add the new method BEFORE `_resolve_provider`. `type:code_generate` `p1` `needs:T211` `model:local`
  - - - - - - - - - - - - - - - Add this method to the `Orchestrator` class:
```
def _run_task_isolated(
self,
task: Task,
plan: str,
session_context: str,
stream_cb: Callable | None,
agent_type: str,
decision: RouteDecision,
) -> str:
from orchid.worker_protocol import TaskContext
from orchid.subprocess_runner import SubprocessRunner
injection_queue = self.session.project_dir / ".orchid" / "inject.queue"
ctx = TaskContext(
task_id=task.id,
task_description=plan,
session_context=session_context,
agent_type=agent_type,
model_key=decision.model,
project_dir=str(self.session.project_dir),
injection_queue_path=str(injection_queue),
)
max_s = cfg.get("isolation.max_task_seconds", 0)
runner = SubprocessRunner()
wresult = runner.run_task_isolated(
ctx=ctx,
stream_callback=stream_cb,
timeout_s=float(max_s) if max_s else None,
)
if not wresult.success:
raise RuntimeError(f"Worker subprocess failed: {wresult.error}")
return wresult.result
```
- [x] **T214** Extend `orchid/orchestrator.py` — wire subprocess opt-in into `_execute_task()`. Read the file first. Find the block where `agent.run(plan)` is called (search for `agent.run(`). Replace the `result = agent.run(plan)` call (or equivalent call to run the agent) with an if/else that checks config. `type:code_generate` `p1` `needs:T213` `model:local`
  - - - - - - - - - - - - - - - Find the line that calls `agent.run(` in `_execute_task()`. Wrap it as follows:
```python
if cfg.get("isolation.subprocess_enabled", False):
result = self._run_task_isolated(
task=task,
plan=plan,
session_context=session_context,
stream_cb=stream_cb,
agent_type=agent_type,
decision=decision,
)
else:
result = agent.run(plan)
```
- [x] **T215** Extend `orchid/agents/base.py` — add `AgentCancelledError` exception class and `cancel_event` attribute. Read the file first. Find the class definitions near the top (look for other exception classes or the BaseAgent class definition around line 288). `type:code_generate` `p1` `model:local`
  - - - - - - - - - - - - - - - Add `import threading` to the imports at the top of the file if not already present
- [x] **T216** Extend `orchid/agents/base.py` — check cancel_event at the top of each ReAct iteration. Read the file first. Find the `run()` method and the `for iteration in range(self.max_iterations):` loop (around line 484). `type:code_generate` `p1` `needs:T215` `model:local`
  - - - - - - - - - - - - - - - Add this check as the FIRST statement inside the for loop body, BEFORE the existing `self._check_injection_queue()` call:
```python
if self._cancel_event.is_set():
raise AgentCancelledError(f"Task cancelled after {iteration} iterations")
```
- [x] **T217** Extend `orchid/orchestrator.py` — start a cancellation timer before calling `agent.run()`. Read the file first. Find where `agent.run(plan)` is called in `_execute_task()` (the `else:` branch added in T214). `type:code_generate` `p1` `needs:T216` `model:local`
  - - - - - - - - - - - - - - - Add these lines BEFORE the `if cfg.get("isolation.subprocess_enabled"...)` block:
```python
# T217: Start wall-clock cancellation timer if max_task_seconds is set
_max_s = cfg.get("isolation.max_task_seconds", 0)
_cancel_timer: threading.Timer | None = None
if _max_s and _max_s > 0 and not cfg.get("isolation.subprocess_enabled", False):
_cancel_timer = threading.Timer(_max_s, agent.cancel)
_cancel_timer.daemon = True
_cancel_timer.start()
```
- [x] **T218** Create `orchid/watchdog.py`. One class: `TaskWatchdog`. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - - - - - - Imports: `import logging, threading, time`. `from orchid.session import Session`. `from orchid.memory.state import TaskStatus`
- [x] **T219** Extend `orchid/runner.py` — wire `TaskWatchdog` into `_run_loop()`. Read the file first. Find `_run_loop()` at line 184. `type:code_generate` `p1` `needs:T218` `model:local`
  - - - - - - - - - - - - - - - Add `from orchid.watchdog import TaskWatchdog` to the imports at the top of the file
- [x] **T220** Extend `orchid/scheduler.py` — add `has_cycle()` to `DependencyGraph`. Read the file first. Find the `DependencyGraph` class (line 53). Add the method after `get_ready_tasks()`. `type:code_generate` `p1` `model:local`
  - - - - - - - - - - - - - - - Add this method to `DependencyGraph`:
```python
def has_cycle(self) -> bool:
"""Return True if the dependency graph contains a cycle (DFS)."""
visited: set[str] = set()
path: set[str] = set()

def _dfs(node: str) -> bool:
visited.add(node)
path.add(node)
for dep in self._deps.get(node, set()):
if dep not in visited:
if _dfs(dep):
return True
elif dep in path:
return True
path.discard(node)
return False

for node in list(self._deps):
if node not in visited:
if _dfs(node):
return True
return False
```
- [x] **T221** Extend `orchid/tools/task_injection.py` — call `has_cycle()` after successful task injection to catch runtime cycles. Read the file first. Find the `inject_task()` function at line 59. `type:code_generate` `p1` `needs:T220` `model:local`
  - - - - - - - - - - - - - - - Add these imports at the top of the file if not already present: `from orchid.scheduler import DependencyGraph, CyclicDependencyError`
- [x] **T227** Review Tier 1 implementation (T209-T226). Check: subprocess isolation compiles and is importable, cancellation token raises AgentCancelledError, watchdog marks stuck tasks BLOCKED, cycle detection finds cycles, all new tests pass. `type:review` `p1` `needs:T222,T223,T224,T225,T226` `model:claude`
  - - - - - - - - - - - - - - - Run `python -c "from orchid.worker_protocol import TaskContext, WorkerEvent, WorkerResult"` — must not error
- [x] **T228** Fix all issues found in T227. Read the T227 result first. Make exactly the fixes listed. `type:code_generate` `p1` `needs:T227` `model:local`
- [x] **T200** Create `orchid/cost/` package with `__init__.py` and `ledger.py` `type:code_generate` `p1` `model:local`
- [x] **T201** Create `orchid/cost/scheduler.py` `type:code_generate` `p1` `needs:T200` `model:local`
- [x] **T202** Add cost config to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `model:local`
- [x] **T203** Wire `CostLedger` token recording into `orchid/orchestrator.py` after agent run `type:code_generate` `p1` `needs:T200,T201,T202` `model:local`
- [x] **T203b** Wire 429 rate-limit detection into `orchid/orchestrator.py` `type:code_generate` `p1` `needs:T203` `model:local`
- [x] **T204** Wire `CostAwareScheduler` into provider resolution in `orchid/orchestrator.py` `type:code_generate` `p1` `needs:T201,T202,T203b` `model:local`
- [x] **T205** Create `tests/test_cost_ledger.py` `type:code_generate` `p1` `needs:T200` `model:local`
- [x] **T206** Create `tests/test_cost_scheduler.py` `type:code_generate` `p1` `needs:T201` `model:local`
- [x] **T207** Review cost scheduling implementation `type:code_review` `p1` `needs:T205,T206,T204`
- [x] **T208** Fix issues found in T207 and add token fields if missing `type:code_generate` `p1` `needs:T207` `model:local`
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
- [x] **T293** Create `tests/test_cron_types.py` (unit tests for ScheduledTask and TaskRun dataclasses). `type:code_generate` `p2` `needs:T285` `model:local`
  - - Imports: `from datetime import UTC, datetime`. `import pytest`. `from orchid.cron.types import ScheduledTask, TaskRun, _new_task_id, _new_run_id, _utcnow`
- `class TestScheduledTask:` with tests:
- `test_default_task_id_format`: `t = ScheduledTask()`. Assert `t.task_id.startswith("stask_")`. Assert `len(t.task_id) == 14` (6 chars for "stask_" + 8 hex chars).
- `test_default_fields`: `t = ScheduledTask(owner_id="u1", name="Daily")`. Assert `t.enabled is True`. Assert `t.schedule == "0 9 * * *"`. Assert `t.task_type == "agent_prompt"`. Assert `t.config == {}`. Assert `t.notify_on_failure is True`. Assert `t.notify_on_success is False`. Assert `t.last_run_at is None`. Assert `t.last_run_status is None`.
- `test_unique_task_ids`: create 100 `ScheduledTask()` instances. Assert all `task_id` values are unique (use a set).
- `test_created_at_is_utc`: `t = ScheduledTask()`. Assert `t.created_at.tzinfo is not None`.
- `test_custom_schedule`: `t = ScheduledTask(schedule="*/5 * * * *")`. Assert `t.schedule == "*/5 * * * *"`.
- `class TestTaskRun:` with tests:
- `test_default_run_id_format`: `r = TaskRun()`. Assert `r.run_id.startswith("run_")`. Assert `len(r.run_id) == 12` (4 chars for "run_" + 8 hex chars).
- `test_default_status_is_running`: `r = TaskRun()`. Assert `r.status == "running"`.
- `test_unique_run_ids`: create 100 `TaskRun()` instances. Assert all `run_id` values are unique.
- `test_fields_settable`: `r = TaskRun(task_id="t1", owner_id="u1", status="success", output="hello")`. Assert `r.task_id == "t1"`. Assert `r.status == "success"`. Assert `r.output == "hello"`.
- Verify: `cd /home/dave/LocalAI/orchid && source .venv/bin/activate && python3 -m pytest tests/test_cron_types.py -v` must pass all tests.
- [x] **T294** Create `tests/test_cron_store.py` (unit tests for TaskRunStore and UserStore scheduled task methods). `type:code_generate` `p2` `needs:T286,T288` `model:local`
  - - Imports: `import pathlib, tempfile`. `from datetime import UTC, datetime, timedelta`. `import pytest`. `from orchid.cron.store import TaskRunStore`. `from orchid.cron.types import TaskRun`. `from orchid.auth.store import UserStore`. `from orchid.auth.types import User`
- `@pytest.fixture def tmp_runs_file(tmp_path): return tmp_path / "runs.jsonl"`
- `@pytest.fixture def run_store(tmp_runs_file): return TaskRunStore(runs_file=tmp_runs_file)`
- `@pytest.fixture def user_store(tmp_path): return UserStore(path=tmp_path / "users.json")`
- `@pytest.fixture def user_with_store(user_store): u = User(user_id="u1",username="alice"); user_store.add_user(u); return user_store`
- `class TestTaskRunStore:` with tests:
- `test_append_and_get_runs(run_store)`: create `TaskRun(task_id="t1",owner_id="u1",status="success")`, append, call `get_runs(task_id="t1")`, assert length 1 and `run.task_id == "t1"`.
- `test_get_runs_filter_by_owner(run_store)`: append runs with `owner_id="u1"` and `owner_id="u2"`. Call `get_runs(owner_id="u1")`. Assert only u1 runs returned.
- `test_get_runs_newest_first(run_store)`: append 3 runs with `task_id="t1"`. Call `get_runs(task_id="t1")`. Assert returned runs are sorted newest `started_at` first.
- `test_get_runs_limit(run_store)`: append 10 runs with `task_id="t1"`. Call `get_runs(task_id="t1", limit=3)`. Assert length == 3.
- `test_get_runs_empty_when_no_file`: create `TaskRunStore(runs_file=pathlib.Path("/tmp/does_not_exist_xyz.jsonl"))`. Call `get_runs()`. Assert result is `[]`.
- `test_prune_removes_old_runs(tmp_runs_file)`: write a run with `started_at = (datetime.now(UTC) - timedelta(days=40)).isoformat()` directly to `tmp_runs_file`. Create `TaskRunStore(runs_file=tmp_runs_file)` (constructor calls `_prune`). Assert `get_runs()` returns `[]`.
- `test_prune_keeps_recent_runs(tmp_runs_file)`: write a run with `started_at = datetime.now(UTC).isoformat()` directly to `tmp_runs_file`. Create store. Assert `get_runs()` returns 1 item.
- `test_append_never_raises(run_store, monkeypatch)`: monkeypatch `run_store._file` to `pathlib.Path("/dev/null/bad/path")`. Call `run_store.append(TaskRun())`. Assert no exception raised.
- `class TestUserStoreScheduledTasks:` with tests:
- `test_upsert_and_get(user_with_store)`: call `user_with_store.upsert_scheduled_task("u1", {"task_id":"stask_00000001","name":"T","enabled":True})`. Call `get_scheduled_task("u1","stask_00000001")`. Assert result is not None and `result["name"]=="T"`.
- `test_upsert_replaces_existing(user_with_store)`: upsert task with `name="Old"`, then upsert same `task_id` with `name="New"`. Get task. Assert `name=="New"`. Assert user has exactly 1 scheduled task.
- `test_delete_task(user_with_store)`: upsert task, then `delete_scheduled_task("u1","stask_00000001")`. Assert get returns None.
- `test_delete_nonexistent_returns_false(user_with_store)`: call `delete_scheduled_task("u1","stask_missing")`. Assert result is False.
- `test_get_all_enabled(user_with_store)`: add second user `u2` to store. Upsert enabled task for u1 and disabled task for u2 (`"enabled":False`). Call `get_all_enabled_scheduled_tasks()`. Assert result contains exactly 1 tuple with `owner_id=="u1"`.
- `test_scheduled_tasks_persisted(tmp_path)`: create store, add user, upsert task, create NEW store from same file, call `get_scheduled_task`. Assert task persists across store reload.
- Verify: `cd /home/dave/LocalAI/orchid && source .venv/bin/activate && python3 -m pytest tests/test_cron_store.py -v` must pass all tests.
- [x] **T295** Create `tests/test_cron_executor.py` (unit tests for TaskExecutor with mocked providers and MCP). `type:code_generate` `p2` `needs:T289` `model:local`
  - - Imports: `from unittest.mock import MagicMock, patch`. `import pytest`. `from orchid.cron.executor import TaskExecutor, TaskExecutionError`
- `@pytest.fixture def executor(): return TaskExecutor()`
- `class TestTaskExecutorAgentPrompt:` with tests:
- `test_missing_prompt_returns_failure(executor)`: call `executor.execute({"task_id":"t1","task_type":"agent_prompt","config":{},"name":"T"},"u1")`. Assert `run.status == "failure"`. Assert `"prompt" in run.error`.
- `test_success_with_mocked_provider(executor)`: use `patch("orchid.providers.registry.get_registry")` to return mock registry whose `resolve()` returns a mock provider whose `complete()` returns `"response text"`. Call execute with `config={"prompt":"hello"}`. Assert `run.status == "success"`. Assert `run.output == "response text"`.
- `test_provider_exception_returns_failure(executor)`: mock provider `complete()` to raise `RuntimeError("boom")`. Call execute. Assert `run.status == "failure"`. Assert `"RuntimeError" in run.error`.
- `class TestTaskExecutorMCPTool:` with tests:
- `test_missing_server_returns_failure(executor)`: call execute with `task_type="mcp_tool"` and `config={"tool":"search","args":{}}`. Assert `run.status == "failure"`. Assert `"server" in run.error`.
- `test_missing_tool_returns_failure(executor)`: call execute with `task_type="mcp_tool"` and `config={"server":"gmail","args":{}}`. Assert `run.status == "failure"`. Assert `"tool" in run.error`.
- `test_success_with_mocked_mcp(executor)`: patch `orchid.mcp.manager.MCPManager` with a mock whose `get_adapter()` returns a mock adapter whose `call_tool()` returns a mock result with `result.content = "found 5 emails"`. Call execute with `task_type="mcp_tool"` and `config={"server":"gmail","tool":"search_threads","args":{"query":"test"}}`. Assert `run.status == "success"`.
- `test_server_not_found_returns_failure(executor)`: patch `MCPManager` so `get_adapter()` returns None. Call execute. Assert `run.status == "failure"`. Assert `"not found" in run.error`.
- `class TestTaskExecutorShell:` with tests:
- `test_missing_command_returns_failure(executor)`: call execute with `task_type="shell"` and `config={}`. Assert `run.status == "failure"`. Assert `"command" in run.error`.
- `test_success_echo(executor)`: call execute with `task_type="shell"` and `config={"command":"echo hello","timeout_sec":10}`. If `run.status == "success"`, assert `"hello" in run.output`. If `run.status == "failure"`, assert `"allowlist" in run.error.lower()`. (echo may be blocked; test must not fail in either case.)
- `class TestTaskExecutorUnknownType:` with tests:
- `test_unknown_type_returns_failure(executor)`: call execute with `task_type="invalid_type"`. Assert `run.status == "failure"`. Assert `"Unknown task_type" in run.error`.
- `class TestTaskExecutorNeverRaises:` with tests:
- `test_execute_never_raises(executor, monkeypatch)`: monkeypatch `TaskExecutor._DISPATCH` to `{"shell": lambda c: (_ for _ in ()).throw(SystemExit(1))}`. Call `executor.execute({"task_id":"t1","task_type":"shell","config":{},"name":"T"},"u1")`. Assert result is a `TaskRun` instance (no exception propagated).
- Verify: `cd /home/dave/LocalAI/orchid && source .venv/bin/activate && python3 -m pytest tests/test_cron_executor.py -v` must pass all tests.
- [x] **T296** Create `tests/test_cron_engine.py` (unit tests for CronEngine). `type:code_generate` `p2` `needs:T290` `model:local`
  - - Imports: `import time`. `from unittest.mock import MagicMock, patch`. `import pytest`. `from orchid.cron.engine import CronEngine, get_engine, reset_engine`
- `@pytest.fixture(autouse=True) def reset(): reset_engine(); yield; reset_engine()`
- `class TestCronEngineSingleton:` with tests:
- `test_get_engine_returns_same_instance`: call `get_engine()` twice. Assert both calls return the same object (`is`).
- `test_reset_engine_creates_new_instance`: `e1 = get_engine()`. Call `reset_engine()`. `e2 = get_engine()`. Assert `e1 is not e2`.
- `class TestCronEngineLifecycle:` with tests:
- `test_start_stop`: patch `orchid.auth.store.get_store` to return a mock store whose `get_all_enabled_scheduled_tasks()` returns `[]`. Create `e = CronEngine()`. Call `e.start()`. Assert `e._scheduler.running is True`. Call `e.stop()`. Assert `e._scheduler.running is False`.
- `test_start_registers_enabled_tasks`: mock store returns `[("u1", {"task_id":"stask_t1","schedule":"0 9 * * *","enabled":True,"name":"T","task_type":"shell","config":{"command":"echo hi"}})]`. Create engine, call `start()`. Assert `engine._scheduler.get_job("stask_t1")` is not None. Call `stop()`.
- `test_invalid_cron_expression_skips_task`: mock store returns task with `"schedule": "not-a-cron"`. Create engine, call `start()`. Assert no job registered (no exception raised). Call `stop()`.
- `class TestCronEngineTaskManagement:` with tests:
- `test_add_or_update_task_enabled`: patch store. Create engine, call `start()`. Call `engine.add_or_update_task("u1", {"task_id":"stask_x","schedule":"0 9 * * *","enabled":True,"name":"T","task_type":"shell","config":{}})`. Assert `engine._scheduler.get_job("stask_x")` is not None. Call `stop()`.
- `test_add_or_update_task_disabled_removes_job`: add enabled task via `add_or_update_task`, then call again with `"enabled": False`. Assert `engine._scheduler.get_job("stask_x")` is None.
- `test_remove_task`: add task, then call `engine.remove_task("stask_x")`. Assert `engine._scheduler.get_job("stask_x")` is None.
- `test_run_now_calls_run_task_in_thread`: patch `CronEngine._run_task` with a MagicMock. Call `engine.run_now("u1", {"task_id":"t1","name":"T"})`. Call `time.sleep(0.2)`. Assert mock was called once with args `("u1", {"task_id":"t1","name":"T"})`.
- Verify: `cd /home/dave/LocalAI/orchid && source .venv/bin/activate && python3 -m pytest tests/test_cron_engine.py -v` must pass all tests.
- [x] **T297** Create `tests/test_cron_api.py` (API endpoint integration tests). `type:code_generate` `p2` `needs:T291,T292` `model:local`
  - - First line: `import os; os.environ.setdefault("JWT_SECRET","test-secret-key-for-unit-tests-only")`
- Imports: `from unittest.mock import MagicMock, patch`. `import pytest`. `from fastapi.testclient import TestClient`. `from orchid.auth.jwt import hash_password, issue_access_token`. `from orchid.auth.store import UserStore`. `from orchid.auth.types import User`
- `@pytest.fixture def tmp_store(tmp_path)`: create `store = UserStore(path=tmp_path/"users.json")`. Add user `User(user_id="u1",username="alice",role="user",password_hash=hash_password("pw"))`. Add user `User(user_id="admin1",username="admin",role="admin",password_hash=hash_password("pw"))`. Return store.
- `@pytest.fixture def app_client(tmp_store, tmp_path)`: use `patch("orchid.auth.store.get_store", return_value=tmp_store)` and `patch("orchid.cron.store.TaskRunStore.__init__", lambda self, **kw: setattr(self, "_file", tmp_path/"runs.jsonl") or setattr(self, "_lock", __import__("threading").Lock()))` and `patch("orchid.cron.engine.get_engine", return_value=MagicMock())` as context managers. Inside the patches, import `from orchid.interfaces.web_server import create_app`. Create `app = create_app(project_paths=[])`. Return `TestClient(app)`. Note: use `with patch(...), patch(...), patch(...):` syntax, yield the client inside the `with` block.
- Helper function `auth_header(user_id: str) -> dict`: return `{"Authorization": f"Bearer {issue_access_token(user_id)}"}`.
- `VALID_TASK_BODY = {"name":"Daily Echo","schedule":"0 9 * * *","task_type":"shell","config":{"command":"echo hi"}}`
- `class TestSchedulerTaskCRUD:` with tests:
- `test_list_tasks_empty(app_client)`: `GET /api/scheduler/tasks` with `auth_header("u1")`. Assert 200. Assert `response.json()["tasks"] == []`.
- `test_create_task(app_client)`: `POST /api/scheduler/tasks` with `VALID_TASK_BODY` and `auth_header("u1")`. Assert 201. Assert `response.json()["task_id"].startswith("stask_")`. Assert `response.json()["owner_id"] == "u1"`.
- `test_create_task_missing_name_returns_400(app_client)`: POST with `{"schedule":"0 9 * * *","task_type":"shell","config":{}}`. Assert 400.
- `test_create_task_invalid_type_returns_400(app_client)`: POST with `VALID_TASK_BODY | {"task_type":"bad_type"}`. Assert 400.
- `test_get_task(app_client)`: create task via POST, extract `task_id` from response. `GET /api/scheduler/tasks/{task_id}`. Assert 200 and `response.json()["task_id"] == task_id`.
- `test_get_nonexistent_task_returns_404(app_client)`: `GET /api/scheduler/tasks/stask_missing`. Assert 404.
- `test_update_task(app_client)`: create task. `PUT /api/scheduler/tasks/{task_id}` with `VALID_TASK_BODY | {"name":"Updated"}`. Assert 200. Assert `response.json()["name"] == "Updated"`.
- `test_delete_task(app_client)`: create task. `DELETE /api/scheduler/tasks/{task_id}`. Assert 200. `GET` same task_id. Assert 404.
- `test_run_now_returns_queued(app_client)`: create task. `POST /api/scheduler/tasks/{task_id}/run`. Assert 200. Assert `response.json()["queued"] is True`.
- `test_list_runs_empty(app_client)`: create task. `GET /api/scheduler/tasks/{task_id}/runs`. Assert 200. Assert `response.json()["runs"] == []`.
- `test_user_cannot_see_other_users_task(app_client, tmp_store)`: as u1, create task, get `task_id`. Add user `u2` to `tmp_store`. `GET /api/scheduler/tasks/{task_id}` with `auth_header("u2")`. Assert 404 (u2 cannot find u1's task).
- `test_admin_sees_all_tasks(app_client)`: as u1, create task. `GET /api/scheduler/tasks` with `auth_header("admin1")`. Assert 200. Assert `len(response.json()["tasks"]) >= 1`. Assert any task has `owner_id == "u1"`.
- `test_unauthenticated_returns_401_or_403(app_client)`: `GET /api/scheduler/tasks` with no auth header. Assert status code in `{401, 403}`.
- Verify: `cd /home/dave/LocalAI/orchid && source .venv/bin/activate && python3 -m pytest tests/test_cron_api.py -v` must pass all tests.
- [x] **T276** Create `tests/test_remote_protocol.py`. Write exactly 4 test functions. `type:code_generate` `p2` `needs:T266` `model:local`
  - - - `test_worker_node_is_available()` — create `WorkerNode(node_id="n1", url="http://x", capacity=4, current_load=0)`, assert `is_available() is True`. Set `current_load=4`, assert `is_available() is False`.
- [x] **T277** Create `tests/test_remote_dispatcher.py`. Write exactly 3 test functions using `unittest.mock.patch`. `type:code_generate` `p2` `needs:T268` `model:local`
  - - - `test_dispatch_posts_to_node_url()` — create two `WorkerNode` objects. Patch `httpx.post` to return a mock response with `json()` returning `{"worker_result_json": WorkerResult(task_id="T001", success=True, result="ok", duration_s=1.0).to_json(), "node_id": "n1"}` and `raise_for_status()` as a no-op. Create `RemoteDispatcher([node1, node2])`. Call `dispatch(ctx)`. Assert `httpx.post` was called once with a URL containing `/task`.
- [x] **T278** Create `tests/test_capability.py`. Write exactly 4 test functions. `type:code_generate` `p2` `needs:T271` `model:local`
  - - - `test_capability_registry_has_all_agent_types()` — import `CAPABILITY_REGISTRY`. Assert all 5 keys exist: `"developer", "tester", "researcher", "reviewer", "base"`.
- [x] **T279** Create `tests/test_export_checkpoint.py`. Write exactly 2 test functions using `tmp_path`. `type:code_generate` `p2` `needs:T274` `model:local`
  - - - `test_export_checkpoint_writes_file(tmp_path)` — create a `CheckpointStore(tmp_path)`, save a checkpoint with minimal data (pass empty lists for tasks/decisions/delegations, `hot_memory=""`, `task_id="T001"`). Get the checkpoint_id from the return value. Call `export_checkpoint(checkpoint_id, tmp_path, tmp_path / "export")`. Assert the exported file exists and `json.loads(exported_path.read_text())["metadata"]["task_id"] == "T001"` (or whatever the structure is — read CheckpointStore.save return type first to understand the checkpoint_id and data structure).
- [x] **T284** Rollup Tier 4 results `type:rollup` `p2` `model:claude` `rollup:T266,T267,T268,T269,T270,T271,T272,T273,T274,T275,T276,T277,T278,T279,T280,T281,T282,T283` `output:TIER4-REPORT.md`
- [x] **T260** Create `tests/test_auth.py`. Write exactly 5 test functions using `tmp_path`. `type:code_generate` `p2` `needs:T249,T250` `model:local`
  - - - - - - - - - - `test_user_dataclass_defaults()` — create `User(user_id="u1", token="tok")`, assert `projects == []` and `api_keys == {}` and `budget_usd == 0.0`
- [x] **T261** Create `tests/test_container_runner.py`. Write exactly 3 test functions. `type:code_generate` `p2` `needs:T254` `model:local`
  - - - - - - - - - - `test_container_runner_unavailable_when_no_docker()` — patch `shutil.which` to return None. Create `ContainerRunner()`. Assert `is_available() is False`.
- [x] **T262** Create `tests/test_user_quota.py`. Write exactly 3 test functions. `type:code_generate` `p2` `needs:T258,T259` `model:local`
  - - - - - - - - - - `test_daily_spend_for_user_sums_correctly(tmp_path)` — create `CostLedger(tmp_path)`. Record two `TokenRecord` objects with `user_id="alice"` and `cost_usd=1.0` each (today's UTC timestamp). Record one with `user_id="bob"` and `cost_usd=5.0`. Assert `ledger.daily_spend_for_user("alice") == 2.0` and `daily_spend_for_user("bob") == 5.0`.
- [x] **T265** Rollup Tier 3 results `type:rollup` `p2` `model:claude` `rollup:T249,T250,T251,T252,T253,T254,T255,T256,T257,T258,T259,T260,T261,T262,T263,T264` `output:TIER3-REPORT.md`
- [x] **T248** Rollup Tier 2 results `type:rollup` `p2` `model:claude` `rollup:T230,T231,T232,T233,T234,T235,T236,T237,T238,T239,T240,T241,T242,T243,T244,T245,T246,T247` `output:TIER2-REPORT.md`
- [x] **T245** Create `tests/test_shell_agent_id.py`. Write exactly 3 test functions. `type:code_generate` `p2` `needs:T239` `model:local`
  - - - - - - - - - - - - - - `test_bash_with_no_agent_id_executes_normally()` — call `bash("echo hello")` with no `agent_id`. Assert result contains "hello".
- [x] **T242** Create `tests/test_file_locks.py`. Write exactly 5 test functions. `type:code_generate` `p2` `needs:T230` `model:local`
  - - - - - - - - - - - - - - `test_acquire_and_release_no_exception()` — create `FileLockRegistry()`, call `acquire("test.py")`, call `release("test.py")`, assert no exception
- [x] **T243** Create `tests/test_react_checkpoint.py`. Write exactly 3 test functions using `tmp_path`. `type:code_generate` `p2` `needs:T232,T233` `model:local`
  - - - - - - - - - - - - - - `test_save_react_checkpoint_writes_file(tmp_path)` — create `CheckpointStore(tmp_path)`, create `ReActCheckpoint(task_id="T001", iteration=5, conversation_history=[{"role": "user", "content": "hi"}])`, call `store.save_react_checkpoint(cp)`, assert the file `tmp_path / "checkpoints" / "react_T001.json"` exists (or wherever the store saves it — check CheckpointStore `__init__` for `_base_dir`)
- [x] **T244** Create `tests/test_mailbox.py`. Write exactly 4 test functions. `type:code_generate` `p2` `needs:T236` `model:local`
  - - - - - - - - - - - - - - `test_send_and_receive()` — get mailbox for "agent-A", send a message with content "hello", call receive, assert `msg.content == "hello"` and `msg.sender == "sender-X"`
- [x] **T222** Create `tests/test_worker_protocol.py`. Write exactly 4 test functions, no fixtures. `type:code_generate` `p2` `needs:T209` `model:local`
  - - - - - - - - - - - - - - - `test_taskcontext_to_json_and_from_json()` — create a `TaskContext` with dummy string values, call `to_json()`, call `from_json()` on the result, assert all fields equal the original
- [x] **T223** Create `tests/test_subprocess_runner.py`. Write exactly 3 test functions using `unittest.mock.patch`. `type:code_generate` `p2` `needs:T211` `model:local`
  - - - - - - - - - - - - - - - `test_run_task_isolated_success()` — patch `subprocess.Popen` to return a mock whose `.stdout` yields two lines: `WorkerEvent(type="agent_step", task_id="T001", payload={"thought":"x"}).to_json()` and `WorkerResult(task_id="T001", success=True, result="done").to_json()`. Patch `.wait()` to return 0. Assert `SubprocessRunner().run_task_isolated(ctx, None, None).success is True`
- [x] **T224** Create `tests/test_agent_cancel.py`. Write exactly 3 test functions. `type:code_generate` `p2` `needs:T215,T216` `model:local`
  - - - - - - - - - - - - - - - `test_cancel_sets_event()` — import `BaseAgent` (or a concrete subclass like `DeveloperAgent`). Create an instance with minimal args (mock project_dir, empty session_context). Call `.cancel()`. Assert `agent._cancel_event.is_set() is True`
- [x] **T225** Create `tests/test_watchdog.py`. Write exactly 4 test functions using `tmp_path` and mocks. `type:code_generate` `p2` `needs:T218` `model:local`
  - - - - - - - - - - - - - - - `test_watchdog_starts_and_stops()` — create a mock Session with `tasks=[]`. Create `TaskWatchdog(session, stuck_threshold_s=60)`. Call `start()` then `stop()`. Assert no exception is raised.
- [x] **T226** Create `tests/test_cycle_detection.py`. Write exactly 3 test functions. `type:code_generate` `p2` `needs:T220` `model:local`
  - - - - - - - - - - - - - - - Import `DependencyGraph, CyclicDependencyError, Scheduler` from `orchid.scheduler`. Use mock Task objects with `id`, `depends_on`, `rollup_sources`, `status`, `priority` attributes.
- [x] **T229** Rollup Tier 1 results `type:rollup` `p2` `model:claude` `rollup:T209,T210,T211,T212,T213,T214,T215,T216,T217,T218,T219,T220,T221,T222,T223,T224,T225,T226,T227,T228` `output:TIER1-REPORT.md`
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
