# Tier 2 — Coordination Tasks
# File advisory locks · Mid-task ReAct checkpoint · Agent mailbox · Shell agent-id · Max-iterations hard cap
# Starts at T230 (after Tier 1 T229). Copy this file content into tasks.md and run.
# Claude Code validates after this tier completes before moving to Tier 3.

## DONE

## TODO

- [ ] **T230** Create `orchid/locks.py`. One class: `FileLockRegistry`. `type:code_generate` `p1` `model:local`
  - Imports: `import threading, logging` from stdlib. `from pathlib import Path` from stdlib. `from collections import defaultdict`
  - `class FileLockRegistry:` — manages per-file threading locks so parallel agents queue behind each other
  - `__init__(self) -> None` — `self._registry: dict[str, threading.Lock] = {}`, `self._meta_lock = threading.Lock()` (protects registry mutations)
  - `_get_lock(self, path: str) -> threading.Lock` — acquires `self._meta_lock`, creates `threading.Lock()` for `path` if not present, returns it
  - `acquire(self, path: str | Path) -> None` — calls `self._get_lock(str(path)).acquire()`
  - `release(self, path: str | Path) -> None` — calls `self._get_lock(str(path)).release()` inside try/except `RuntimeError` (already unlocked — log warning, continue)
  - `lock(self, path: str | Path)` — context manager using `contextlib.contextmanager`: yields after `acquire(path)`, calls `release(path)` in finally
  - Add `import contextlib` to imports
  - Module-level singleton: `_registry = FileLockRegistry()` and `def get_file_lock_registry() -> FileLockRegistry: return _registry`
  - Verify: `grep -n "class FileLockRegistry\|def acquire\|def release\|def lock\|get_file_lock_registry" orchid/locks.py` must return 5 lines

- [ ] **T231** Extend `orchid/tools/filesystem.py` — use `FileLockRegistry` in `write_file()` and `append_file()`. Read the file first. `type:code_generate` `p1` `model:local` `needs:T230`
  - Add import at the top: `from orchid.locks import get_file_lock_registry`
  - In `write_file(path: str, content: str) -> str`: wrap the file write operation with the lock registry. Before the write, call `get_file_lock_registry().acquire(path)`. After the write (in finally), call `get_file_lock_registry().release(path)`. Use try/finally to guarantee release.
  - In `append_file(path: str, content: str) -> str`: apply the same acquire/release pattern around the file append operation.
  - Do not change the return values or any other logic.
  - Verify: `grep -n "get_file_lock_registry\|acquire\|release" orchid/tools/filesystem.py` must return at least 4 lines

- [ ] **T232** Extend `orchid/checkpoint/schema.py` — add `ReActCheckpoint` dataclass. Read the file first. Find the end of the file (after existing dataclasses). `type:code_generate` `p1` `model:local`
  - Add this dataclass at the end of the file (after existing definitions):
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
  - Make sure `ReActCheckpoint` is importable from `orchid.checkpoint.schema`
  - Verify: `grep -n "class ReActCheckpoint\|conversation_history\|partial_result" orchid/checkpoint/schema.py` must return 3 lines

- [ ] **T233** Extend `orchid/checkpoint/store.py` — add `save_react_checkpoint()` and `load_react_checkpoint()` methods. Read the file first. Add after the `prune()` method. `type:code_generate` `p1` `model:local` `needs:T232`
  - Add `from orchid.checkpoint.schema import ReActCheckpoint` to the imports (check if schema is already imported; if so, add `ReActCheckpoint` to the existing import)
  - Add this method to `CheckpointStore`:
    ```
    def save_react_checkpoint(self, cp: ReActCheckpoint) -> Path:
        """Save a mid-task ReAct checkpoint. Overwrites previous checkpoint for same task_id."""
        from datetime import datetime, UTC
        cp.timestamp = datetime.now(UTC).isoformat()
        dest = self._base_dir / f"react_{cp.task_id}.json"
        dest.write_text(json.dumps(asdict(cp)))
        return dest
    ```
  - Add this method to `CheckpointStore`:
    ```
    def load_react_checkpoint(self, task_id: str) -> ReActCheckpoint | None:
        """Load a mid-task ReAct checkpoint for task_id, or None if not found."""
        path = self._base_dir / f"react_{task_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return ReActCheckpoint(**data)
    ```
  - Verify: `grep -n "def save_react_checkpoint\|def load_react_checkpoint" orchid/checkpoint/store.py` must return 2 lines

- [ ] **T234** Extend `orchid/agents/base.py` — save a ReAct checkpoint every 5 iterations. Read the file first. Find the `run()` method and the `for iteration in range(self.max_iterations):` loop. `type:code_generate` `p1` `model:local` `needs:T233`
  - At the TOP of `BaseAgent.__init__()`, add: `self._checkpoint_store: Any = None` (use `from typing import Any` if not already imported)
  - Add a method: `def set_checkpoint_store(self, store: Any) -> None: self._checkpoint_store = store`
  - Inside the ReAct `for` loop, AFTER the existing cancel_event check and `_check_injection_queue()` call, add:
    ```python
    # T234: Save mid-task checkpoint every 5 iterations
    if self._checkpoint_store is not None and iteration > 0 and iteration % 5 == 0:
        from orchid.checkpoint.schema import ReActCheckpoint
        _cp = ReActCheckpoint(
            task_id=getattr(self, "_current_task_id", "unknown"),
            iteration=iteration,
            conversation_history=[{"role": m.role, "content": m.content} for m in self.history],
        )
        try:
            self._checkpoint_store.save_react_checkpoint(_cp)
        except Exception as _cp_err:
            logger.debug("ReAct checkpoint failed at iter %d: %s", iteration, _cp_err)
    ```
  - In `BaseAgent.__init__()`, also add: `self._current_task_id: str = "unknown"`
  - Verify: `grep -n "set_checkpoint_store\|_checkpoint_store\|save_react_checkpoint\|_current_task_id" orchid/agents/base.py` must return at least 4 lines

- [ ] **T235** Extend `orchid/orchestrator.py` — wire checkpoint_store and task_id into agent before run. Read the file first. Find the block where `agent` is assigned (via `self._get_agent(...)`) and before `agent.run(plan)` is called. `type:code_generate` `p1` `model:local` `needs:T234`
  - After `agent = self._get_agent(...)` and BEFORE the `if cfg.get("isolation.subprocess_enabled"...)` block, add:
    ```python
    # T235: Wire ReAct checkpoint store and task_id into agent
    try:
        from orchid.checkpoint.store import CheckpointStore
        agent.set_checkpoint_store(CheckpointStore(self.session.project_dir))
        agent._current_task_id = task.id
    except Exception as _cs_err:
        logger.debug("Could not wire checkpoint store into agent: %s", _cs_err)
    ```
  - Verify: `grep -n "set_checkpoint_store\|_current_task_id" orchid/orchestrator.py` must return 2 lines

- [ ] **T236** Create `orchid/mailbox.py`. One class: `AgentMailbox`. `type:code_generate` `p1` `model:local`
  - Imports: `import queue, threading, logging` from stdlib. `from dataclasses import dataclass, field`. `from typing import Any`
  - `@dataclass class MailboxMessage: sender: str; content: Any; timestamp: float = field(default_factory=...)` — use `import time` and `default_factory=time.monotonic`
  - `class AgentMailbox:` — thread-safe message queue per agent instance
  - `__init__(self, agent_id: str) -> None` — `self.agent_id = agent_id`, `self._queue: queue.Queue[MailboxMessage] = queue.Queue()`
  - `send(self, sender: str, content: Any) -> None` — puts `MailboxMessage(sender=sender, content=content)` onto the queue
  - `receive(self, timeout_s: float = 0.0) -> MailboxMessage | None` — calls `self._queue.get(timeout=timeout_s)` inside try/except `queue.Empty` returning None
  - `has_messages(self) -> bool` — returns `not self._queue.empty()`
  - `drain(self) -> list[MailboxMessage]` — collects all current messages without blocking: loop while `has_messages()`, call `receive()`, collect into list, return list
  - Module-level: `_mailboxes: dict[str, AgentMailbox] = {}` and `_lock = threading.Lock()`
  - `def get_mailbox(agent_id: str) -> AgentMailbox:` — creates mailbox if not exists (thread-safe via `_lock`), returns it
  - `def drop_mailbox(agent_id: str) -> None:` — removes mailbox from `_mailboxes` if present (thread-safe)
  - Verify: `grep -n "class AgentMailbox\|class MailboxMessage\|def get_mailbox\|def drop_mailbox" orchid/mailbox.py` must return 4 lines

- [ ] **T237** Extend `orchid/agents/base.py` — add `send_message` and `receive_message` tools. Read the file first. Find `_make_project_tools()` method. `type:code_generate` `p1` `model:local` `needs:T236`
  - Add `from orchid.mailbox import get_mailbox` import at the top of the file
  - In `__init__()`, add: `self._mailbox_id: str = f"{self.__class__.__name__}-{id(self)}"`
  - In `_make_project_tools()` (the method that builds the tools dict), add these two entries to the tools dict:
    ```python
    "send_message": lambda agent_id, content: (
        get_mailbox(agent_id).send(sender=self._mailbox_id, content=content) or
        f"Message sent to {agent_id}"
    ),
    "receive_message": lambda timeout_s=0.0: (
        lambda msg: msg.content if msg else None
    )(get_mailbox(self._mailbox_id).receive(timeout_s=float(timeout_s))),
    ```
  - Verify: `grep -n "send_message\|receive_message\|_mailbox_id" orchid/agents/base.py` must return at least 3 lines

- [ ] **T238** Extend `orchid/orchestrator.py` — drop agent mailbox at task end. Read the file first. Find the `finally:` block inside `_execute_task()` (the block that runs after the agent finishes). `type:code_generate` `p1` `model:local` `needs:T237`
  - In the `finally:` block of `_execute_task()`, add:
    ```python
    # T238: Clean up agent mailbox
    try:
        from orchid.mailbox import drop_mailbox
        if hasattr(agent, "_mailbox_id"):
            drop_mailbox(agent._mailbox_id)
    except Exception:
        pass
    ```
  - Verify: `grep -n "drop_mailbox" orchid/orchestrator.py` must return 1 line

- [ ] **T239** Extend `orchid/tools/shell.py` — add `agent_id` parameter to `bash()`. Read the file first. Find `def bash(command: str, timeout: int | None = None) -> str:` at line 120. `type:code_generate` `p1` `model:local`
  - Change the signature to: `def bash(command: str, timeout: int | None = None, agent_id: str = "") -> str:`
  - At the TOP of `bash()`, BEFORE the existing blocklist/allowlist checks, add:
    ```python
    # T239: Per-agent shell capability check
    if agent_id:
        from orchid.agents.base import _get_agent_allowed_tools
        allowed = _get_agent_allowed_tools(agent_id)
        if allowed is not None and "bash" not in allowed:
            return f"[permission denied] Agent '{agent_id}' does not have bash access"
    ```
  - Add this module-level function to `orchid/agents/base.py` (read base.py first, add after `cancel()` method): `def _get_agent_allowed_tools(agent_id: str) -> frozenset[str] | None: """ Return the allowed_tools frozenset for an agent instance by its class name prefix, or None if unrestricted. """ # agent_id is typically "ClassName-<id(self)>" class_name = agent_id.split("-")[0].lower() # Map class name to known frozensets _AGENT_TOOL_MAP = {"testeragent": TesterAgent.allowed_tools, "revieweragent": ReviewerAgent.allowed_tools, "researcheragent": ResearcherAgent.allowed_tools} return _AGENT_TOOL_MAP.get(class_name, None)`
  - Note: TesterAgent, ReviewerAgent, ResearcherAgent are importable from their modules — add those imports inside the function body to avoid circular imports
  - Verify: `grep -n "agent_id\|_get_agent_allowed_tools" orchid/tools/shell.py` must return at least 2 lines

- [ ] **T240** Add `agents.max_iterations` config block to `orchid/orchid.defaults.yaml`. Read the file first. Find the `agents:` section. Add under it. `type:code_generate` `p1` `model:local`
  - Under the `agents:` key (after existing agent config lines), add:
    ```yaml
      max_iterations:          # per-agent-type hard cap on ReAct iterations (0 = use agents.max_react_iterations)
        developer: 0
        tester: 0
        researcher: 0
        reviewer: 0
        base: 0
    ```
  - Verify: `grep -n "max_iterations" orchid/orchid.defaults.yaml` must return at least 1 line

- [ ] **T241** Extend `orchid/agents/base.py` — read per-agent-type `max_iterations` from config and enforce hard cap. Read the file first. Find `__init__()`. `type:code_generate` `p1` `model:local` `needs:T240`
  - In `__init__()`, AFTER `self.max_iterations = cfg.get("agents.max_react_iterations", 25)`, add:
    ```python
    # T241: Per-agent-type hard cap from agents.max_iterations config
    _agent_type_key = self.__class__.__name__.lower().replace("agent", "")
    _hard_cap = cfg.get(f"agents.max_iterations.{_agent_type_key}", 0)
    if _hard_cap and _hard_cap > 0:
        self.max_iterations = _hard_cap
    ```
  - Verify: `grep -n "max_iterations.*_agent_type_key\|_hard_cap" orchid/agents/base.py` must return at least 2 lines

- [ ] **T242** Create `tests/test_file_locks.py`. Write exactly 5 test functions. `type:code_generate` `p2` `model:local` `needs:T230`
  - `test_acquire_and_release_no_exception()` — create `FileLockRegistry()`, call `acquire("test.py")`, call `release("test.py")`, assert no exception
  - `test_lock_context_manager_no_exception()` — use `with registry.lock("file.txt"):` block, assert no exception
  - `test_different_paths_have_different_locks()` — acquire lock for "a.py", acquire lock for "b.py", assert they are different `threading.Lock` objects
  - `test_same_path_returns_same_lock()` — call `_get_lock("same.py")` twice, assert they are the same object
  - `test_release_unlocked_does_not_raise()` — call `release("never_acquired.py")` without acquiring first, assert no exception (warning is logged but no exception raised)
  - Import `FileLockRegistry` from `orchid.locks`
  - Verify: run `python -m pytest tests/test_file_locks.py -q` — all 5 must pass

- [ ] **T243** Create `tests/test_react_checkpoint.py`. Write exactly 3 test functions using `tmp_path`. `type:code_generate` `p2` `model:local` `needs:T232,T233`
  - `test_save_react_checkpoint_writes_file(tmp_path)` — create `CheckpointStore(tmp_path)`, create `ReActCheckpoint(task_id="T001", iteration=5, conversation_history=[{"role": "user", "content": "hi"}])`, call `store.save_react_checkpoint(cp)`, assert the file `tmp_path / "checkpoints" / "react_T001.json"` exists (or wherever the store saves it — check CheckpointStore `__init__` for `_base_dir`)
  - `test_load_react_checkpoint_returns_none_if_missing(tmp_path)` — create `CheckpointStore(tmp_path)`, call `load_react_checkpoint("NOTEXIST")`, assert result is None
  - `test_save_and_load_react_checkpoint_roundtrip(tmp_path)` — save a checkpoint, load it back, assert `loaded.task_id == "T001"` and `loaded.iteration == 5` and `loaded.conversation_history == [{"role": "user", "content": "hi"}]`
  - Verify: run `python -m pytest tests/test_react_checkpoint.py -q` — all 3 must pass

- [ ] **T244** Create `tests/test_mailbox.py`. Write exactly 4 test functions. `type:code_generate` `p2` `model:local` `needs:T236`
  - `test_send_and_receive()` — get mailbox for "agent-A", send a message with content "hello", call receive, assert `msg.content == "hello"` and `msg.sender == "sender-X"`
  - `test_receive_returns_none_when_empty()` — get mailbox for "agent-B", call `receive(timeout_s=0.0)`, assert result is None
  - `test_has_messages()` — empty mailbox: `has_messages()` is False. After `send()`: `has_messages()` is True.
  - `test_drop_mailbox_removes_it()` — get mailbox, send message, drop it, get again (should be a new empty mailbox), assert `has_messages() is False`
  - Import `get_mailbox, drop_mailbox` from `orchid.mailbox`
  - After each test, call `drop_mailbox` to clean up (avoid state leakage between tests)
  - Verify: run `python -m pytest tests/test_mailbox.py -q` — all 4 must pass

- [ ] **T245** Create `tests/test_shell_agent_id.py`. Write exactly 3 test functions. `type:code_generate` `p2` `model:local` `needs:T239`
  - `test_bash_with_no_agent_id_executes_normally()` — call `bash("echo hello")` with no `agent_id`. Assert result contains "hello".
  - `test_bash_with_unrestricted_agent_executes()` — call `bash("echo hi", agent_id="DeveloperAgent-123")`. Since DeveloperAgent has no frozenset (unrestricted), assert result contains "hi".
  - `test_bash_with_restricted_agent_returns_permission_denied()` — patch `_get_agent_allowed_tools` to return `frozenset({"read_file"})` (no "bash"). Call `bash("echo test", agent_id="ReviewerAgent-456")`. Assert `"permission denied"` in result.
  - Import `bash` from `orchid.tools.shell`
  - Verify: run `python -m pytest tests/test_shell_agent_id.py -q` — all 3 must pass

- [ ] **T246** Review Tier 2 implementation (T230-T245). Check: file locks are thread-safe, mid-task checkpoint saves/loads correctly, mailbox is thread-safe, shell permission check works, max_iterations hard cap is read correctly. `type:review` `p1` `model:claude` `needs:T242,T243,T244,T245`
  - Run `python -c "from orchid.locks import FileLockRegistry, get_file_lock_registry"` — must not error
  - Run `python -c "from orchid.mailbox import AgentMailbox, get_mailbox, drop_mailbox"` — must not error
  - Run `python -c "from orchid.checkpoint.schema import ReActCheckpoint"` — must not error
  - Run `python -m pytest tests/test_file_locks.py tests/test_react_checkpoint.py tests/test_mailbox.py tests/test_shell_agent_id.py -q` — all must pass
  - Report PASS or FAIL for each check with the error message if FAIL

- [ ] **T247** Fix all issues found in T246. Read the T246 result first. Make exactly the fixes listed. `type:code_generate` `p1` `model:local` `needs:T246`

- [ ] **T248** Rollup Tier 2 results `type:rollup` `rollup:T230,T231,T232,T233,T234,T235,T236,T237,T238,T239,T240,T241,T242,T243,T244,T245,T246,T247` `output:TIER2-REPORT.md` `model:claude`
