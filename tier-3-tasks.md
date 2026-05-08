# Tier 3 — Security and Multi-Tenancy Tasks
# User auth · Per-user API key scoping · Container isolation · File write audit trail · Per-user quota enforcement
# Starts at T249 (after Tier 2 T248). Copy this file content into tasks.md and run.
# Claude Code validates after this tier completes before moving to Tier 4.

## DONE

## TODO

- [ ] **T249** Create `orchid/auth/__init__.py` with content `# Orchid auth layer` and `orchid/auth/types.py`. Define 2 things in `types.py`. `type:code_generate` `p1` `model:local`
  - `orchid/auth/__init__.py` content: exactly `# Orchid auth layer`
  - `orchid/auth/types.py` imports: `from dataclasses import dataclass, field`
  - `@dataclass class User:` with fields: `user_id: str`, `token: str`, `projects: list[str] = field(default_factory=list)`, `api_keys: dict[str, str] = field(default_factory=dict)` (maps provider name → API key), `budget_usd: float = 0.0` (0 = use global budget)
  - `class AuthError(Exception): """Raised on authentication failure."""`
  - Both must be importable from `orchid.auth.types`
  - Verify: `grep -n "class User\|class AuthError\|api_keys\|budget_usd" orchid/auth/types.py` must return 4 lines

- [ ] **T250** Create `orchid/auth/store.py`. One class: `UserStore`. `type:code_generate` `p1` `model:local` `needs:T249`
  - Imports: `import json, logging, threading` from stdlib. `from pathlib import Path`. `from orchid.auth.types import User, AuthError`
  - `class UserStore:` — reads/writes `~/.config/orchid/users.json`
  - `__init__(self, path: Path | None = None) -> None` — `self._path = path or Path.home() / ".config" / "orchid" / "users.json"`, `self._lock = threading.Lock()`, `self._users: dict[str, User] = {}`, call `self._load()`
  - `_load(self) -> None` — if `self._path.exists()`, read JSON (`{"users": [{user dict}, ...]}` format), reconstruct `User` objects into `self._users` keyed by `user.user_id`. On any error log and continue.
  - `_save(self) -> None` — write `{"users": [dataclasses.asdict(u) for u in self._users.values()]}` to `self._path`. Create parent dirs if needed (`self._path.parent.mkdir(parents=True, exist_ok=True)`).
  - `get_by_token(self, token: str) -> User` — iterate `self._users.values()`, return user where `user.token == token`. Raise `AuthError("Invalid token")` if not found.
  - `get_by_id(self, user_id: str) -> User` — return `self._users[user_id]` or raise `AuthError(f"User {user_id} not found")`
  - `add_user(self, user: User) -> None` — thread-safe: acquire lock, add to `self._users`, call `_save()`
  - `remove_user(self, user_id: str) -> None` — thread-safe: acquire lock, pop from `self._users`, call `_save()`
  - Verify: `grep -n "class UserStore\|def get_by_token\|def get_by_id\|def add_user\|def remove_user" orchid/auth/store.py` must return 5 lines

- [ ] **T251** Create `orchid/auth/middleware.py`. FastAPI dependency for token-based auth. `type:code_generate` `p1` `model:local` `needs:T250`
  - Imports: `from fastapi import Depends, HTTPException, status`. `from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials`. `from orchid.auth.store import UserStore`. `from orchid.auth.types import User, AuthError`
  - `_store = UserStore()` module-level singleton
  - `_bearer = HTTPBearer(auto_error=False)` module-level
  - `async def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> User:` — if `credentials` is None, raise `HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")`. Try `return _store.get_by_token(credentials.credentials)`. On `AuthError`, raise `HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")`.
  - `async def get_optional_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> User | None:` — same but returns None instead of raising if no credentials or invalid token.
  - Verify: `grep -n "get_current_user\|get_optional_user\|HTTPBearer\|UserStore" orchid/auth/middleware.py` must return 4 lines

- [ ] **T252** Extend `orchid/web/server.py` — add auth endpoints and optional auth guard. Read the file first. Find the FastAPI `app` instance. `type:code_generate` `p1` `model:local` `needs:T251`
  - Add these imports near the top: `from orchid.auth.store import UserStore`. `from orchid.auth.types import User, AuthError`. `from orchid.auth.middleware import get_optional_user`
  - Add a module-level `_user_store = UserStore()`
  - Add `POST /api/auth/token` endpoint: body is `{"user_id": str, "token": str}` (use a Pydantic `BaseModel` or plain dict). Look up user by `_user_store.get_by_token(token)`. Return `{"user_id": user.user_id, "valid": True}` on success. On `AuthError` return HTTP 401.
  - Add `GET /api/auth/me` endpoint: `Depends(get_optional_user)` — returns user info if authenticated, or `{"authenticated": False}` if not.
  - Do not add auth guards to existing project endpoints yet (that would break unauthenticated web UI).
  - Verify: `grep -n "auth/token\|auth/me\|_user_store\|UserStore" orchid/web/server.py` must return at least 3 lines

- [ ] **T253** Extend `orchid/providers/registry.py` — accept per-user API keys that override env/config keys. Read the file first. Find the provider resolution logic. `type:code_generate` `p1` `model:local`
  - Find the main resolution function or class (likely `resolve_provider()` or `ProviderRegistry`). Add a parameter `user_api_keys: dict[str, str] | None = None` to the resolution function/method.
  - In the resolution logic, BEFORE checking env vars or config for API keys: if `user_api_keys` is not None and the target provider name is in `user_api_keys`, use that key instead of the env/config key.
  - If the function sets env vars temporarily (e.g., `os.environ["ANTHROPIC_API_KEY"] = key`), apply the user key the same way. If it passes keys directly to provider constructors, pass the user key there.
  - This change must be backward-compatible: passing `user_api_keys=None` must behave identically to the current code.
  - Verify: `grep -n "user_api_keys" orchid/providers/registry.py` must return at least 2 lines

- [ ] **T254** Create `orchid/container_runner.py`. One class: `ContainerRunner`. Opt-in; skips gracefully if Docker unavailable. `type:code_generate` `p1` `model:local`
  - Imports: `import json, logging, shutil, subprocess, sys` from stdlib. `from pathlib import Path`. `from orchid.worker_protocol import TaskContext, WorkerResult`
  - `class ContainerRunnerError(Exception): pass`
  - `class ContainerRunner:` — wraps `docker run` to execute the worker subprocess inside a container
  - `DOCKER_IMAGE: str = "python:3.12-slim"` — class variable
  - `__init__(self, image: str | None = None) -> None` — `self._image = image or self.DOCKER_IMAGE`. Check Docker is available: `self._docker_available = shutil.which("docker") is not None`
  - `is_available(self) -> bool` — returns `self._docker_available`
  - `run_task_isolated(self, ctx: TaskContext, stream_callback=None, timeout_s: float | None = None) -> WorkerResult:` — if not `self._docker_available`, raise `ContainerRunnerError("docker not found on PATH")`. Build command: `["docker", "run", "--rm", "-i", "--network=none", self._image, "python", "-m", "orchid.worker_subprocess"]`. Spawn with `subprocess.Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True)`. Write `ctx.to_json() + "\n"` to stdin, close it. Read stdout line by line same as `SubprocessRunner.run_task_isolated()`: route WorkerEvent lines to stream_callback, capture final WorkerResult line. Call `proc.wait(timeout=int(timeout_s) if timeout_s else None)`. Return WorkerResult. On `TimeoutExpired`: kill proc, return error WorkerResult.
  - Verify: `grep -n "class ContainerRunner\|class ContainerRunnerError\|def is_available\|def run_task_isolated\|DOCKER_IMAGE" orchid/container_runner.py` must return 5 lines

- [ ] **T255** Extend `orchid/subprocess_runner.py` — if `isolation.container_enabled` is true, use `ContainerRunner` instead of bare subprocess. Read the file first. `type:code_generate` `p1` `model:local` `needs:T254`
  - Add import at top: `from orchid.config import cfg`
  - In `SubprocessRunner.run_task_isolated()`, BEFORE spawning the subprocess, add:
    ```python
    if cfg.get("isolation.container_enabled", False):
        from orchid.container_runner import ContainerRunner
        _cr = ContainerRunner()
        if _cr.is_available():
            return _cr.run_task_isolated(ctx, stream_callback=stream_callback, timeout_s=timeout_s)
        else:
            logger.warning("container_enabled=true but docker not found — falling back to subprocess")
    ```
  - Verify: `grep -n "container_enabled\|ContainerRunner" orchid/subprocess_runner.py` must return at least 2 lines

- [ ] **T256** Extend `orchid/hooks/audit.py` — add `log_file_write()` function to `AuditLogger`. Read the file first. Find the `AuditLogger` class. Add the method after the last existing log method. `type:code_generate` `p1` `model:local`
  - Add this method to `AuditLogger`:
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
  - Also add a module-level convenience function after the class:
    ```python
    def log_file_write(task_id: str, path: str, agent_id: str = "", bytes_written: int = 0, operation: str = "write") -> None:
        """Module-level convenience wrapper around the singleton AuditLogger."""
        get_audit_logger().log_file_write(task_id=task_id, path=path, agent_id=agent_id, bytes_written=bytes_written, operation=operation)
    ```
  - (Check if `get_audit_logger()` exists; if not, add it as a singleton getter similar to the existing pattern)
  - Verify: `grep -n "def log_file_write" orchid/hooks/audit.py` must return 2 lines (one on AuditLogger, one module-level)

- [ ] **T257** Extend `orchid/tools/filesystem.py` — call `log_file_write()` after every successful `write_file()` and `append_file()`. Read the file first. `type:code_generate` `p1` `model:local` `needs:T256`
  - Add import: `from orchid.hooks.audit import log_file_write as _audit_file_write`
  - In `write_file(path, content)`: after the file write succeeds (inside the try, after the write), add:
    ```python
    try:
        _audit_file_write(task_id="", path=path, bytes_written=len(content.encode()), operation="write")
    except Exception:
        pass
    ```
  - In `append_file(path, content)`: same pattern with `operation="append"`
  - Verify: `grep -n "_audit_file_write\|log_file_write" orchid/tools/filesystem.py` must return at least 2 lines

- [ ] **T258** Extend `orchid/cost/ledger.py` — add `user_id` field to `TokenRecord` and `daily_spend_for_user()` method to `CostLedger`. Read the file first. `type:code_generate` `p1` `model:local`
  - Find `@dataclass class TokenRecord:` (line 32). Add `user_id: str = ""` as the LAST field (with default so existing code constructing `TokenRecord` without it still works).
  - Find `class CostLedger:` (line 91). Add this method after `daily_spend()`:
    ```python
    def daily_spend_for_user(self, user_id: str) -> float:
        """Return total USD spend today for a specific user_id. UTC date."""
        from datetime import datetime, UTC
        today = datetime.now(UTC).date().isoformat()
        return sum(
            r.cost_usd
            for r in self._records
            if r.user_id == user_id and r.timestamp.startswith(today)
        )
    ```
  - Verify: `grep -n "user_id\|daily_spend_for_user" orchid/cost/ledger.py` must return at least 3 lines

- [ ] **T259** Extend `orchid/cost/scheduler.py` — add `check_user_budget()` method. Read the file first. Find `CostScheduler` class. Add after `check_budget()`. `type:code_generate` `p1` `model:local` `needs:T258`
  - Add this method to `CostScheduler`:
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
  - Verify: `grep -n "def check_user_budget" orchid/cost/scheduler.py` must return 1 line

- [ ] **T260** Create `tests/test_auth.py`. Write exactly 5 test functions using `tmp_path`. `type:code_generate` `p2` `model:local` `needs:T249,T250`
  - `test_user_dataclass_defaults()` — create `User(user_id="u1", token="tok")`, assert `projects == []` and `api_keys == {}` and `budget_usd == 0.0`
  - `test_userstore_add_and_get_by_token(tmp_path)` — create `UserStore(tmp_path / "users.json")`, add `User(user_id="u1", token="secret")`, call `get_by_token("secret")`, assert `user.user_id == "u1"`
  - `test_userstore_invalid_token_raises(tmp_path)` — create UserStore, call `get_by_token("wrong")`, assert raises `AuthError`
  - `test_userstore_remove_user(tmp_path)` — add user, remove it, assert `get_by_id("u1")` raises `AuthError`
  - `test_userstore_persists_to_disk(tmp_path)` — add user, create a new `UserStore` from same path, assert `get_by_token("secret")` succeeds (proves data was saved and reloaded)
  - Import `User, AuthError` from `orchid.auth.types`. Import `UserStore` from `orchid.auth.store`
  - Verify: run `python -m pytest tests/test_auth.py -q` — all 5 must pass

- [ ] **T261** Create `tests/test_container_runner.py`. Write exactly 3 test functions. `type:code_generate` `p2` `model:local` `needs:T254`
  - `test_container_runner_unavailable_when_no_docker()` — patch `shutil.which` to return None. Create `ContainerRunner()`. Assert `is_available() is False`.
  - `test_container_runner_raises_when_unavailable()` — patch `shutil.which` to return None. Call `run_task_isolated(ctx, None, None)`. Assert raises `ContainerRunnerError`.
  - `test_container_runner_is_available_when_docker_present()` — patch `shutil.which` to return `/usr/bin/docker`. Create `ContainerRunner()`. Assert `is_available() is True`.
  - Add `pytest.mark.skipif` at module level: `pytestmark = pytest.mark.unit` (or just no skip — these tests mock docker so they run anywhere)
  - Import `ContainerRunner, ContainerRunnerError` from `orchid.container_runner`. Import `TaskContext` from `orchid.worker_protocol`.
  - Build a dummy `TaskContext` using all-string dummy values for the required fields.
  - Verify: run `python -m pytest tests/test_container_runner.py -q` — all 3 must pass

- [ ] **T262** Create `tests/test_user_quota.py`. Write exactly 3 test functions. `type:code_generate` `p2` `model:local` `needs:T258,T259`
  - `test_daily_spend_for_user_sums_correctly(tmp_path)` — create `CostLedger(tmp_path)`. Record two `TokenRecord` objects with `user_id="alice"` and `cost_usd=1.0` each (today's UTC timestamp). Record one with `user_id="bob"` and `cost_usd=5.0`. Assert `ledger.daily_spend_for_user("alice") == 2.0` and `daily_spend_for_user("bob") == 5.0`.
  - `test_check_user_budget_raises_when_exceeded()` — create a mock CostScheduler (or real one with tmp_path). Set up ledger with user "alice" having spent $9.50 today. Call `check_user_budget("alice", 10.0)` — must not raise. Add $1.00 more. Call `check_user_budget("alice", 10.0)` — must raise `BudgetBlockedError`.
  - `test_check_user_budget_no_limit_when_zero()` — call `check_user_budget("anyone", 0.0)` — must not raise regardless of spend.
  - Import `CostLedger` from `orchid.cost.ledger`. Import `CostScheduler, BudgetBlockedError` from `orchid.cost.scheduler`. Import `TokenRecord` from `orchid.cost.ledger`.
  - Verify: run `python -m pytest tests/test_user_quota.py -q` — all 3 must pass

- [ ] **T263** Review Tier 3 implementation (T249-T262). Check: auth layer is importable, UserStore persists correctly, container runner handles unavailable docker gracefully, file write audit fires, user quota enforcement works. `type:review` `p1` `model:claude` `needs:T260,T261,T262`
  - Run `python -c "from orchid.auth.types import User, AuthError; from orchid.auth.store import UserStore; from orchid.auth.middleware import get_current_user"` — must not error
  - Run `python -c "from orchid.container_runner import ContainerRunner, ContainerRunnerError"` — must not error
  - Run `python -c "from orchid.hooks.audit import log_file_write"` — must not error
  - Run `python -c "from orchid.cost.scheduler import CostScheduler; cs = CostScheduler.__new__(CostScheduler); print(hasattr(cs, 'check_user_budget'))"` — must print True
  - Run `python -m pytest tests/test_auth.py tests/test_container_runner.py tests/test_user_quota.py -q` — all must pass
  - Report PASS or FAIL for each check with the error message if FAIL

- [ ] **T264** Fix all issues found in T263. Read the T263 result first. Make exactly the fixes listed. `type:code_generate` `p1` `model:local` `needs:T263`

- [ ] **T265** Rollup Tier 3 results `type:rollup` `rollup:T249,T250,T251,T252,T253,T254,T255,T256,T257,T258,T259,T260,T261,T262,T263,T264` `output:TIER3-REPORT.md` `model:claude`
