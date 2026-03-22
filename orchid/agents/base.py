"""Base agent — ReAct loop (Reason → Act → Observe) with pluggable tools."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchid import config as cfg
from orchid.tools.models import Message, call

logger = logging.getLogger(__name__)

# Tool registry type
ToolFn = Callable[..., str]

# ── Built-in tools ────────────────────────────────────────────────────────────
from orchid.tools.consistency import check_imports_summary
from orchid.tools.filesystem import append_file, list_dir, read_file, write_file
from orchid.tools.shell import bash

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

    return {
        "read_file": _rr_file,
        "write_file": _rw_file,
        "append_file": _ra_file,
        "list_dir": _rl_dir,
        "bash": bash,
        "check_imports": _check_imports,
        "get_task_files": _get_task_files,
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

    # If True, the run loop will refuse to accept a Final Answer until at least
    # one write_file, append_file, or bash call has succeeded.  DeveloperAgent sets
    # this to True so the local model can't declare done without writing anything.
    _require_file_write: bool = False

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
        self.session_context = session_context
        self.history: list[Message] = []
        self.max_iterations = cfg.get("agents.max_react_iterations", 15)
        # Delegation — set by AgentDelegator when spawning sub-agents
        self.delegator: Any = None
        self.delegation_depth: int = 0
        # Streaming and injection
        self.stream_callback = stream_callback
        self.injection_queue_path = (
            Path(injection_queue_path) if injection_queue_path else None
        )

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
        self.history = [Message("user", task_description)]
        logger.info("[%s] Starting task: %s", self.__class__.__name__, task_description[:80])

        _did_write = False        # tracks whether any write_file/append_file/bash ran
        _write_reminders = 0      # how many times we've already nudged the model

        for iteration in range(self.max_iterations):
            # Check for injected context before each iteration
            self._check_injection_queue()

            response = call(
                messages=self.history,
                model_key=self.model_key,
                system=self.system_prompt(),
            )
            self.history.append(Message("assistant", response))
            logger.debug("[%s] iter=%d response=%s", self.__class__.__name__, iteration, response[:200])

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
                observation = self._dispatch(tool_name, tool_args)
                # Track successful writes so _require_file_write enforcement works
                if tool_name in ("write_file", "append_file", "bash") and not observation.startswith("["):
                    _did_write = True

            logger.debug("[%s] Tool %s → %s", self.__class__.__name__, tool_name, observation[:200])
            self.history.append(Message("user", f"Observation: {observation}"))

            # Stream iteration data
            if self.stream_callback:
                thought_m = _THOUGHT_RE.search(response)
                self.stream_callback({
                    "iter": iteration,
                    "thought": thought_m.group(1).strip() if thought_m else "",
                    "action": tool_name or "",
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
