"""Base agent — ReAct loop (Reason → Act → Observe) with pluggable tools."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchid import config as cfg
from orchid.hooks.events import HookEvent
from orchid.hooks.registry import HookRegistry
from orchid.tools.models import Message, call

logger = logging.getLogger(__name__)

# Tool registry type
ToolFn = Callable[..., str]

# ── Built-in tools ────────────────────────────────────────────────────────────
from orchid.tools.consistency import check_imports_summary
from orchid.tools.filesystem import append_file, list_dir, read_file, write_file
from orchid.tools.shell import bash, detect_environment, rewrite_python_command

_BUILTIN_TOOLS: dict[str, ToolFn] = {
    "read_file": read_file,
    "write_file": write_file,
    "append_file": append_file,
    "list_dir": list_dir,
    "bash": bash,
    "check_imports": check_imports_summary,
}


def _make_project_tools(project_dir: Path) -> dict[str, ToolFn]:
    """Return filesystem tool wrappers that anchor relative paths to project_dir.

    Absolute paths that fall outside the project directory are rejected with a
    clear error message so the agent can self-correct rather than writing to
    Docker container paths, site-packages, or the service cwd.
    """

    def _resolve(path: str) -> Path | str:
        """Return resolved Path, or an error string if the path is disallowed."""
        p = Path(path)
        if not p.is_absolute():
            return (project_dir / p).resolve()
        resolved = p.resolve()
        try:
            resolved.relative_to(project_dir.resolve())
            return resolved
        except ValueError:
            return (
                f"[path error: '{path}' is outside the project directory. "
                f"Use a relative path or an absolute path under {project_dir}]"
            )

    def _rw_file(path: str, content: str) -> str:
        target = _resolve(path)
        if isinstance(target, str):
            return target
        return write_file(str(target), content)

    def _ra_file(path: str, content: str) -> str:
        target = _resolve(path)
        if isinstance(target, str):
            return target
        return append_file(str(target), content)

    def _rr_file(path: str) -> str:
        target = _resolve(path)
        if isinstance(target, str):
            return target
        return read_file(str(target))

    def _rl_dir(path: str = ".") -> str:
        target = _resolve(path)
        if isinstance(target, str):
            return target
        return list_dir(str(target))

    def _check_imports(path: str = ".") -> str:
        target = _resolve(path)
        if isinstance(target, str):
            return target
        return check_imports_summary(str(target))

    def _get_task_files(task_id: str) -> str:
        return _get_task_files_for_project(task_id, project_dir)

    def _project_bash(command: str, timeout: int | None = None) -> str:
        """Bash wrapper that rewrites python/pytest/pip to use the project venv."""
        rewritten = rewrite_python_command(command, project_dir)
        return bash(rewritten, timeout=timeout)

    from orchid.tools.task_injection import spawn_task as _spawn_task_fn

    def _send_message(agent_id: str, content: str) -> str:
        from orchid.mailbox import get_mailbox
        mailbox = get_mailbox(agent_id)
        mailbox.send(sender="agent", content=content)
        return f"Message sent to agent '{agent_id}'"

    def _receive_message(agent_id: str, timeout_s: float = 0.0) -> str:
        from orchid.mailbox import get_mailbox
        mailbox = get_mailbox(agent_id)
        msg = mailbox.receive(timeout_s=timeout_s)
        if msg is None:
            return "[no messages in mailbox]"
        return f"From {msg.sender}: {msg.content}"

    return {
        "read_file": _rr_file,
        "write_file": _rw_file,
        "append_file": _ra_file,
        "list_dir": _rl_dir,
        "bash": _project_bash,
        "check_imports": _check_imports,
        "get_task_files": _get_task_files,
        "spawn_task": _spawn_task_fn,
        "send_message": _send_message,
        "receive_message": _receive_message,
    }

def _get_task_files_for_project(task_id: str, project_dir: Path) -> str:
    """Read session logs and return files created/modified by a task."""
    import json as _json
    log_dir = project_dir / ".orchid" / "session_logs"
    if not log_dir.exists():
        return f"No session logs found in {log_dir}"
    for log_file in sorted(log_dir.glob("*.jsonl"), reverse=True):
        try:
            for line in log_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = _json.loads(line)
                if record.get("type") == "task_manifest" and record.get("task_id") == task_id:
                    created = record.get("files_created", [])
                    modified = record.get("files_modified", [])
                    parts = [f"Files for {task_id}:"]
                    if created:
                        parts.append(f"  created: {', '.join(created)}")
                    if modified:
                        parts.append(f"  modified: {', '.join(modified)}")
                    return "\n".join(parts) if len(parts) > 1 else f"No files recorded for {task_id}"
        except Exception:
            continue
    return f"No file manifest found for task {task_id}"


_SEARCH_SCHEMAS = [
    {
        "name": "search",
        "description": "Search the web for information. Use Action: search[query] or Action Input: {\"query\": \"...\"}.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "fetch",
        "description": "Fetch and extract text content from a URL. Use Action: fetch[url] or Action Input: {\"url\": \"...\"}.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
]

_DELEGATE_SCHEMA = {
    "name": "delegate",
    "description": (
        "Spawn a sub-agent to handle a focused subtask. "
        "Use Action: delegate[agent_type | task description]. "
        "agent_type: developer | researcher | reviewer | base. "
        "Example: Action: delegate[researcher | find the best Python library for PDF parsing]"
    ),
}

_BUILTIN_SCHEMAS = [
    {
        "name": "read_file",
        "description": "Read the full contents of a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "write_file",
        "description": "Write content to a file, overwriting if it exists.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "append_file",
        "description": "Append content to a file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_dir",
        "description": "List the contents of a directory.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "required": []},
    },
    {
        "name": "bash",
        "description": "Execute a shell command and return its output.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    },
    {
        "name": "check_imports",
        "description": "Scan project files for broken relative imports. Use Action: check_imports[path] where path is the project directory (default '.').",
        "parameters": {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "required": []},
    },
    {
        "name": "get_task_files",
        "description": "Return files created/modified by a task. Use Action: get_task_files[TASK_ID].",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    },
]


# ── ReAct action parsing ──────────────────────────────────────────────────────

# Standard JSON-arg format:  Action: tool_name\nAction Input: {...}
# NOTE: greedy .*  so that } inside string values (JS code, JSON files) doesn't
# truncate the match prematurely.
_ACTION_RE = re.compile(
    r"Action:\s*(\w+)\s*\nAction Input:\s*(\{.*\})",
    re.DOTALL,
)
# Shorthand bracket format:  Action: search[query]  or  Action: fetch[url]
_ACTION_BRACKET_RE = re.compile(
    r"Action:\s*(\w+)\[([^\]]+)\]",
)
# Heredoc-style write format — avoids JSON-encoding file content entirely:
#   Action: write_file
#   Action Path: src/foo.js
#   Action Content:
#   <<<ORCHID
#   ...file content...
#   ORCHID
_WRITE_HEREDOC_RE = re.compile(
    r"Action:\s*write_file\s*\nAction Path:\s*(.+?)\s*\nAction Content:\s*\n<<<ORCHID\n(.*?)\nORCHID",
    re.DOTALL,
)
# Single-path format — the model sometimes generates:
#   Action: list_dir
#   Action Path: src/
# (learned from the heredoc example).  Handle it for any single-path tool.
_ACTION_PATH_RE = re.compile(
    r"Action:\s*(\w+)\s*\nAction Path:\s*([^\n]+)",
)
_FINAL_RE = re.compile(r"Final Answer:\s*(.*)", re.DOTALL)
_THOUGHT_RE = re.compile(r"Thought:\s*(.*?)(?=\nAction:|\nFinal Answer:|$)", re.DOTALL)

# Map tool names to their primary argument name for bracket and path formats
_TOOL_ARG_MAP: dict[str, str] = {
    "read_file": "path",
    "list_dir": "path",
    "append_file": "path",
    "write_file": "path",
    "bash": "command",
    "search": "query",
    "fetch": "url",
    "check_imports": "path",
    "get_task_files": "task_id",
}


def _extract_json_object(text: str) -> str | None:
    """Return the first complete JSON object from text using brace-depth tracking.

    More robust than a regex: correctly handles } inside string values so that
    code files, package.json, etc. don't cause premature truncation.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


class AgentCancelledError(Exception):
    """Raised when the agent's cancel_event is set mid-run."""


class BaseAgent:
    """
    ReAct agent base class.

    Subclasses may:
    - Override system_prompt() to specialise persona/instructions
    - Add extra tools via register_tool()
    - Override model_key to change routing
    """

    model_key: str = "local"
    agent_type: str = "base"
    agent_name: str = "base"  # maps to providers.<agent_name> in .orchid.yaml

    # If True, the run loop will refuse to accept a Final Answer until at least
    # one write_file, append_file, or bash call has succeeded.  DeveloperAgent sets
    # this to True so the local model can't declare done without writing anything.
    _require_file_write: bool = False

    # Restrict which tools this agent type may call. None = unrestricted.
    # Subclasses set a frozenset; config key agents.allowed_tools.<agent_type>
    # overrides the class default at instantiation time.
    allowed_tools: frozenset[str] | None = None

    def __init__(
        self,
        extra_tools: dict[str, ToolFn] | None = None,
        session_context: str = "",
        stream_callback: Callable[[dict[str, Any]], None] | None = None,
        injection_queue_path: str | Path | None = None,
        project_dir: str | Path | None = None,
    ):
        self.project_dir: Path | None = Path(project_dir).resolve() if project_dir else None
        base_tools = (
            _make_project_tools(self.project_dir)
            if self.project_dir else _BUILTIN_TOOLS
        )
        self.tools: dict[str, ToolFn] = {**base_tools, **(extra_tools or {})}
        # T080: detect project environment for prompt injection
        self.environment: str = (
            detect_environment(self.project_dir) if self.project_dir else "unknown"
        )
        self.session_context = session_context
        self.history: list[Message] = []
        self.max_iterations = cfg.get("agents.max_react_iterations", 25)
        # T241: Per-agent-type hard cap from agents.max_iterations config
        _agent_type_key = self.__class__.__name__.lower().replace("agent", "")
        _hard_cap = cfg.get(f"agents.max_iterations.{_agent_type_key}", 0)
        if _hard_cap and _hard_cap > 0:
            self.max_iterations = _hard_cap
        self._cancel_event: threading.Event = threading.Event()
        self._mailbox_id: str = f"{self.__class__.__name__}-{id(self)}"
        # T234: ReAct checkpoint store — set by orchestrator before run()
        self._checkpoint_store: Any = None
        # Phase 2: checkpoint to resume from (set by orchestrator on recovery)
        self._resume_checkpoint: Any = None
        # Phase 4: suspend/resume events
        self._suspend_event: threading.Event = threading.Event()
        self._resume_event: threading.Event = threading.Event()
        self._suspended: bool = False
        # Delegation — set by AgentDelegator when spawning sub-agents
        self.delegator: Any = None
        self.delegation_depth: int = 0
        # Streaming and injection
        self.stream_callback = stream_callback
        self.injection_queue_path = (
            Path(injection_queue_path) if injection_queue_path else None
        )
        # Apply tool capability restrictions
        _config_allowed = cfg.get("agents.allowed_tools", {})
        if isinstance(_config_allowed, dict):
            _config_tools = _config_allowed.get(self.agent_type, None)
        else:
            _config_tools = None
        if _config_tools:
            _allowed: frozenset[str] | None = frozenset(_config_tools)
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

        # T272: Override allowed_tools from AgentCapability registry if capability is stricter
        try:
            from orchid.capability import get_capability
            _cap = get_capability(self.__class__.__name__.lower().replace("agent", ""))
            if _cap.allowed_tools is not None:
                _cap_removed = [k for k in list(self.tools) if k not in _cap.allowed_tools]
                for _k in _cap_removed:
                    del self.tools[_k]
                if _cap_removed:
                    logger.debug("[%s] capability restricted; removed: %s", self.__class__.__name__, _cap_removed)
            if _cap.max_iterations > 0 and self.max_iterations > _cap.max_iterations:
                self.max_iterations = _cap.max_iterations
        except Exception as _cap_err:
            logger.debug("Capability registry lookup failed: %s", _cap_err)

    def cancel(self) -> None:
        """Signal the agent to stop after the current iteration."""
        self._cancel_event.set()

    def suspend(self) -> None:
        """Ask the agent to pause at the next iteration boundary."""
        self._resume_event.clear()
        self._suspend_event.set()

    def resume(self) -> None:
        """Wake a suspended agent."""
        self._suspend_event.clear()
        self._resume_event.set()

    def set_checkpoint_store(self, store: Any) -> None:
        """Wire a CheckpointStore into this agent for mid-task ReAct checkpointing."""
        self._checkpoint_store = store


    def register_tool(self, name: str, fn: ToolFn) -> None:
        self.tools[name] = fn

    def system_prompt(self) -> str:
        tool_list = "\n".join(f"- {s['name']}: {s['description']}" for s in _BUILTIN_SCHEMAS)
        delegation_section = ""
        if self.delegator is not None:
            delegation_section = (
                "\n## Delegation\n"
                f"- {_DELEGATE_SCHEMA['name']}: {_DELEGATE_SCHEMA['description']}\n"
                "Use bracket format: Action: delegate[agent_type | task description]\n"
            )
        return (
            "You are a helpful AI agent working inside the Orchid orchestration framework.\n\n"
            "## Available Tools\n"
            f"{tool_list}\n"
            f"{delegation_section}\n"
            "## ReAct Format\n"
            "Think step by step. When you need to use a tool, respond with:\n"
            "Thought: <your reasoning>\n"
            "Action: <tool_name>\n"
            "Action Input: {\"arg\": \"value\"}\n\n"
            "After receiving the observation, continue reasoning until you can answer with:\n"
            "Final Answer: <your answer>\n\n"
            "## Tool Call Formats\n"
            "Use EXACTLY one of these formats — no other format is recognised.\n\n"
            "read_file / list_dir / bash:\n"
            "  Action: read_file\n"
            "  Action Input: {\"path\": \"src/server.js\"}\n\n"
            "  Action: list_dir\n"
            "  Action Input: {\"path\": \"src\"}\n\n"
            "  Action: bash\n"
            "  Action Input: {\"command\": \"ls src\"}\n\n"
            "write_file (use this for ALL file writes — avoids JSON encoding problems):\n"
            "  Action: write_file\n"
            "  Action Path: src/server.js\n"
            "  Action Content:\n"
            "  <<<ORCHID\n"
            "  <complete file content here — no escaping needed>\n"
            "  ORCHID\n\n"
            "CRITICAL: Do NOT use 'Action Path:' for read_file or list_dir — "
            "that format is ONLY valid for write_file. "
            "For everything except write_file, use 'Action Input: {\"key\": \"value\"}'.\n\n"
            + (
                f"## Working Directory\n"
                f"Project directory: {self.project_dir}\n"
                f"All relative paths resolve to this directory. "
                f"Do NOT use absolute paths like /app, /root, or /home/user — "
                f"use relative paths (e.g. src/server.js) or the full project path above.\n\n"
                if self.project_dir else ""
            )
            + "## Project Context\n"
            f"{self.session_context}"
            + self._environment_prompt_section()
            + self._verify_syntax_only_section()
        )

    def _environment_prompt_section(self) -> str:
        """T080: inject detected project environment into system prompt."""
        env = cfg.get("agents.environment", None) or self.environment
        if env == "unknown" or not self.project_dir:
            return ""
        _runner_hints = {
            "docker": (
                "Use: docker compose exec <service> python -m pytest\n"
                "Do NOT use bare python3 or pip install inside the container."
            ),
            "venv": (
                "Use: .venv/bin/python -m pytest (or .venv/bin/pytest)\n"
                "Do NOT use bare python3 or pip — they lack project packages."
            ),
            "node": (
                "Use: npm test  or  npx jest\n"
                "Do NOT use bare node for test execution."
            ),
            "python": (
                "Use: python3 -m pytest  or the configured test runner.\n"
                "Install deps with pip if needed."
            ),
        }
        hint = _runner_hints.get(env, "")
        return f"\n\n## Project Environment\nDetected: {env}\n{hint}"

    def _verify_syntax_only_section(self) -> str:
        """T081: inject verify_syntax_only mode instruction when enabled."""
        if not cfg.get("agents.verify_syntax_only", False):
            return ""
        return (
            "\n\n## Verification Mode: SYNTAX ONLY\n"
            "Do NOT run pytest, jest, make test, or docker exec.\n"
            "Only verify syntax using:\n"
            "- Python: python3 -m py_compile <file>\n"
            "- JS/TS: node --check <file>\n"
            "- TypeScript: tsc --noEmit\n"
            "Mark task complete after syntax check passes."
        )

    def _check_injection_queue(self) -> None:
        """Prepend any pending injected context to history."""
        if not self.injection_queue_path or not self.injection_queue_path.exists():
            return
        try:
            text = self.injection_queue_path.read_text(encoding="utf-8").strip()
            if not text:
                return
            # Clear the queue
            self.injection_queue_path.write_text("", encoding="utf-8")
            inject_msg = f"## Injected context from user:\n{text}"
            self.history.append(Message("user", inject_msg))
            logger.info("[%s] Injected context applied: %s", self.__class__.__name__, text[:100])
        except Exception as exc:
            logger.debug("Failed to read injection queue: %s", exc)

    def run(self, task_description: str) -> str:
        """Execute the ReAct loop for a given task. Returns the final answer."""
        from orchid import shutdown as _shutdown

        # Phase 2: resume from a saved ReAct checkpoint if one was set
        _resume = self._resume_checkpoint
        if _resume is not None:
            self.history = [Message(m["role"], m["content"]) for m in _resume.conversation_history]
            _start_iteration = _resume.iteration + 1
            logger.info("[%s] Resuming task %s from iteration %d",
                        self.__class__.__name__, _resume.task_id, _resume.iteration)
        else:
            self.history = [Message("user", task_description)]
            _start_iteration = 0

        logger.info("[%s] Starting task: %s", self.__class__.__name__, task_description[:80])

        _did_write = False        # tracks whether any write_file/append_file/bash ran
        _write_reminders = 0      # how many times we've already nudged the model
        _slow_iters = 0           # Phase 6: consecutive slow-iteration counter
        _max_iter_s = cfg.get("agents.max_iteration_seconds", 0)

        for iteration in range(_start_iteration, self.max_iterations):
            # Cancellation check — process-wide shutdown OR per-agent cancel
            if self._cancel_event.is_set() or _shutdown.is_shutting_down():
                # Save a final checkpoint before raising so restart recovery can pick up here
                if self._checkpoint_store is not None:
                    from orchid.checkpoint.schema import ReActCheckpoint
                    _final_cp = ReActCheckpoint(
                        task_id=getattr(self, "_current_task_id", "unknown"),
                        iteration=iteration,
                        conversation_history=[{"role": m.role, "content": m.content}
                                              for m in self.history],
                    )
                    try:
                        self._checkpoint_store.save_react_checkpoint(_final_cp)
                    except Exception as _e:
                        logger.debug("Final checkpoint on cancel failed: %s", _e)
                reason = "shutdown" if _shutdown.is_shutting_down() else "cancelled"
                raise AgentCancelledError(f"Task {reason} after {iteration} iterations")

            # Phase 4: suspend — park the agent until resume() is called
            if self._suspend_event.is_set():
                if self._checkpoint_store is not None:
                    from orchid.checkpoint.schema import ReActCheckpoint
                    _susp_cp = ReActCheckpoint(
                        task_id=getattr(self, "_current_task_id", "unknown"),
                        iteration=iteration,
                        conversation_history=[{"role": m.role, "content": m.content}
                                              for m in self.history],
                    )
                    try:
                        self._checkpoint_store.save_react_checkpoint(_susp_cp)
                    except Exception:
                        pass
                self._suspended = True
                logger.info("[%s] Suspended at iteration %d", self.__class__.__name__, iteration)
                self._resume_event.wait()   # blocks until resume() sets this
                self._suspended = False
                logger.info("[%s] Resumed at iteration %d", self.__class__.__name__, iteration)

            self._check_injection_queue()

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

            _iter_start = time.monotonic()
            response = call(
                messages=self.history,
                model_key=self.model_key,
                system=self.system_prompt(),
            )
            _iter_elapsed = time.monotonic() - _iter_start
            self.history.append(Message("assistant", response))
            logger.debug("[%s] iter=%d response=%s", self.__class__.__name__, iteration, response[:200])

            # Phase 6: per-iteration latency budget
            if _max_iter_s and _max_iter_s > 0 and _iter_elapsed > _max_iter_s:
                _slow_iters += 1
                logger.warning(
                    "[%s] Slow iteration %d: %.1fs > %.0fs limit (%d/3 strikes)",
                    self.__class__.__name__, iteration, _iter_elapsed, _max_iter_s, _slow_iters,
                )
                if _slow_iters >= 3:
                    if self._checkpoint_store is not None:
                        from orchid.checkpoint.schema import ReActCheckpoint
                        _lat_cp = ReActCheckpoint(
                            task_id=getattr(self, "_current_task_id", "unknown"),
                            iteration=iteration,
                            conversation_history=[{"role": m.role, "content": m.content}
                                                  for m in self.history],
                        )
                        try:
                            self._checkpoint_store.save_react_checkpoint(_lat_cp)
                        except Exception:
                            pass
                    raise AgentCancelledError(
                        f"Iteration latency budget exceeded: {_slow_iters} consecutive "
                        f"iterations > {_max_iter_s}s"
                    )
            else:
                _slow_iters = 0  # reset on a fast iteration

            # Check for final answer
            final_m = _FINAL_RE.search(response)
            if final_m:
                answer = final_m.group(1).strip()
                # If this agent requires file writes and none happened yet, refuse the
                # Final Answer and remind the model to actually write the files.
                if self._require_file_write and not _did_write and _write_reminders < 2:
                    _write_reminders += 1
                    logger.warning(
                        "[%s] Final Answer at iter %d with no writes — injecting reminder (%d/2)",
                        self.__class__.__name__, iteration, _write_reminders,
                    )
                    reminder = (
                        "You declared a Final Answer but have not written any files yet. "
                        "This task is NOT complete until you call write_file (or bash) to "
                        "create the actual code on disk.\n\n"
                        "Do NOT describe what should be written — write it now using:\n"
                        "Action: write_file\n"
                        "Action Path: <relative/path/to/file>\n"
                        "Action Content:\n"
                        "<<<ORCHID\n"
                        "<complete file content>\n"
                        "ORCHID\n\n"
                        "Write each required file, then give your Final Answer."
                    )
                    self.history.append(Message("user", f"Observation: {reminder}"))
                    continue

                logger.info("[%s] Final answer at iter %d", self.__class__.__name__, iteration)
                if self.stream_callback:
                    thought_m = _THOUGHT_RE.search(response)
                    self.stream_callback({
                        "iter": iteration,
                        "thought": thought_m.group(1).strip() if thought_m else "",
                        "action": "final_answer",
                        "action_input": "",
                        "observation": answer[:200],
                        "timestamp": datetime.now(UTC).isoformat(),
                    })
                return answer

            # Check for action — priority: heredoc > JSON > bracket > single-path
            heredoc_m = _WRITE_HEREDOC_RE.search(response)
            action_m = _ACTION_RE.search(response)
            bracket_m = _ACTION_BRACKET_RE.search(response)
            path_m = _ACTION_PATH_RE.search(response)

            if heredoc_m:
                # Heredoc format: content is not JSON-encoded, safe for code files
                tool_name = "write_file"
                tool_args = {"path": heredoc_m.group(1).strip(), "content": heredoc_m.group(2)}
                observation = None
            elif action_m:
                tool_name = action_m.group(1)
                raw_json = _extract_json_object(action_m.group(2)) or action_m.group(2)
                try:
                    tool_args = json.loads(raw_json)
                except json.JSONDecodeError as e:
                    observation = (
                        f"[parse error in Action Input: {e}]\n"
                        "Your Action Input JSON is malformed — likely due to unescaped quotes or "
                        "curly braces in file content. Use the heredoc format instead:\n"
                        "Action: write_file\n"
                        "Action Path: path/to/file\n"
                        "Action Content:\n"
                        "<<<ORCHID\n"
                        "<file content here — no JSON encoding needed>\n"
                        "ORCHID"
                    )
                    tool_name = None  # skip dispatch
                else:
                    observation = None
            elif bracket_m:
                tool_name = bracket_m.group(1)
                arg_value = bracket_m.group(2).strip()
                arg_key = _TOOL_ARG_MAP.get(tool_name, "input")
                tool_args = {arg_key: arg_value}
                observation = None
            elif path_m:
                # Model used "Action Path:" for a non-write-file tool (e.g. list_dir, read_file)
                tool_name = path_m.group(1)
                arg_value = path_m.group(2).strip()
                arg_key = _TOOL_ARG_MAP.get(tool_name, "path")
                tool_args = {arg_key: arg_value}
                observation = None
            else:
                # No structured action and no Final Answer.
                # If writes are required and none happened, nudge rather than accepting as done.
                if self._require_file_write and not _did_write and _write_reminders < 2:
                    _write_reminders += 1
                    logger.warning(
                        "[%s] No action parsed at iter %d, no writes yet — injecting reminder (%d/2)",
                        self.__class__.__name__, iteration, _write_reminders,
                    )
                    reminder = (
                        "I could not parse a tool call from your response. "
                        "You have not written any files yet — this task is not complete.\n\n"
                        "To write a file use EXACTLY this format:\n"
                        "Thought: <reasoning>\n"
                        "Action: write_file\n"
                        "Action Path: relative/path/to/file.js\n"
                        "Action Content:\n"
                        "<<<ORCHID\n"
                        "<complete file content here>\n"
                        "ORCHID\n\n"
                        "To read a file:  Action: read_file\nAction Input: {\"path\": \"src/server.js\"}\n"
                        "To list a dir:   Action: list_dir\nAction Input: {\"path\": \"src\"}\n"
                        "To run a command: Action: bash\nAction Input: {\"command\": \"ls src\"}"
                    )
                    self.history.append(Message("user", f"Observation: {reminder}"))
                    continue
                return response.strip()

            if observation is None:
                _registry = HookRegistry()
                _pre_result = _registry.fire(HookEvent.pre_tool_use_event(
                    tool=tool_name or "",
                    input_data=tool_args or {},
                ))
                if _pre_result.blocked:
                    observation = f"[BLOCKED by hook: {_pre_result.error or 'hook blocked this action'}]"
                else:
                    observation = self._dispatch(tool_name, tool_args)
                    _registry.fire(HookEvent.post_tool_use_event(
                        tool=tool_name or "",
                        input_data=tool_args or {},
                        output=observation,
                    ))
                    # Track successful writes so _require_file_write enforcement works
                    if tool_name in ("write_file", "append_file", "bash") and not observation.startswith("["):
                        _did_write = True

            logger.debug("[%s] Tool %s → %s", self.__class__.__name__, tool_name, observation[:200])
            self.history.append(Message("user", f"Observation: {observation}"))

            # Stream iteration data
            if self.stream_callback:
                thought_m = _THOUGHT_RE.search(response)
                # Exclude large content fields (write_file body) from action_input
                _input_args = {k: v for k, v in (tool_args or {}).items() if k != "content"}
                self.stream_callback({
                    "iter": iteration,
                    "thought": thought_m.group(1).strip() if thought_m else "",
                    "action": tool_name or "",
                    "action_input": json.dumps(_input_args),
                    "observation": observation[:300],
                    "timestamp": datetime.now(UTC).isoformat(),
                })

        return "[max iterations reached without final answer]"

    def _do_delegate(self, args: dict[str, Any]) -> str:
        if self.delegator is None:
            return "[delegation not available: no delegator configured for this agent]"
        raw = args.get("input", "").strip()
        if "|" not in raw:
            return "[delegate error: expected format delegate[agent_type | task description]]"
        agent_type, task = raw.split("|", 1)
        agent_type = agent_type.strip()
        task = task.strip()
        if not agent_type or not task:
            return "[delegate error: agent_type and task are both required]"
        return self.delegator.delegate(
            agent_type=agent_type,
            task=task,
            context=self.session_context,
            depth=self.delegation_depth,
            parent_agent=self.__class__.__name__,
        )

    def _dispatch(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "delegate":
            return self._do_delegate(args)
        fn = self.tools.get(tool_name)
        if fn is None:
            return f"[unknown tool: {tool_name}]"
        try:
            timeout = cfg.get("agents.tool_timeout_seconds", 30)
            from concurrent.futures import ThreadPoolExecutor
            from concurrent.futures import TimeoutError as FuturesTimeout
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(fn, **args)
                try:
                    result = future.result(timeout=timeout)
                except FuturesTimeout:
                    future.cancel()
                    return f"[tool timeout after {timeout}s: {tool_name}]"
            return str(result)
        except Exception as e:
            return f"[tool error: {e}]"


def _get_agent_allowed_tools(agent_id: str) -> frozenset[str] | None:
    """Return the allowed_tools frozenset for an agent instance by its class name prefix, or None if unrestricted."""
    # agent_id is typically "ClassName-<id(self)>"
    class_name = agent_id.split("-")[0].lower()
    from orchid.agents.researcher import ResearcherAgent
    from orchid.agents.reviewer import ReviewerAgent
    from orchid.agents.tester import TesterAgent
    _AGENT_TOOL_MAP = {
        "testeragent": TesterAgent.allowed_tools,
        "revieweragent": ReviewerAgent.allowed_tools,
        "researcheragent": ResearcherAgent.allowed_tools,
    }
    return _AGENT_TOOL_MAP.get(class_name, None)
