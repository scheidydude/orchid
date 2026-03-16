"""Base agent — ReAct loop (Reason → Act → Observe) with pluggable tools."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from orchid import config as cfg
from orchid.tools.models import Message, call

logger = logging.getLogger(__name__)

# Tool registry type
ToolFn = Callable[..., str]

# ── Built-in tools ────────────────────────────────────────────────────────────
from orchid.tools.filesystem import read_file, write_file, list_dir, append_file
from orchid.tools.shell import bash


_BUILTIN_TOOLS: dict[str, ToolFn] = {
    "read_file": read_file,
    "write_file": write_file,
    "append_file": append_file,
    "list_dir": list_dir,
    "bash": bash,
}

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
]


# ── ReAct action parsing ──────────────────────────────────────────────────────

# Standard JSON-arg format:  Action: tool_name\nAction Input: {...}
_ACTION_RE = re.compile(
    r"Action:\s*(\w+)\s*\nAction Input:\s*(\{.*?\})",
    re.DOTALL,
)
# Shorthand bracket format:  Action: search[query]  or  Action: fetch[url]
_ACTION_BRACKET_RE = re.compile(
    r"Action:\s*(\w+)\[([^\]]+)\]",
)
_FINAL_RE = re.compile(r"Final Answer:\s*(.*)", re.DOTALL)
_THOUGHT_RE = re.compile(r"Thought:\s*(.*?)(?=\nAction:|\nFinal Answer:|$)", re.DOTALL)


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

    def __init__(
        self,
        extra_tools: dict[str, ToolFn] | None = None,
        session_context: str = "",
        stream_callback: Callable[[dict[str, Any]], None] | None = None,
        injection_queue_path: str | Path | None = None,
    ):
        self.tools: dict[str, ToolFn] = {**_BUILTIN_TOOLS, **(extra_tools or {})}
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
            "## Project Context\n"
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
                logger.info("[%s] Final answer at iter %d", self.__class__.__name__, iteration)
                if self.stream_callback:
                    thought_m = _THOUGHT_RE.search(response)
                    self.stream_callback({
                        "iter": iteration,
                        "thought": thought_m.group(1).strip() if thought_m else "",
                        "action": "final_answer",
                        "observation": answer[:200],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                return answer

            # Check for action — try JSON format first, then bracket shorthand
            action_m = _ACTION_RE.search(response)
            bracket_m = _ACTION_BRACKET_RE.search(response)

            if action_m:
                tool_name = action_m.group(1)
                try:
                    tool_args = json.loads(action_m.group(2))
                except json.JSONDecodeError as e:
                    observation = f"[parse error in Action Input: {e}]"
                    tool_name = None  # skip dispatch
                else:
                    observation = None
            elif bracket_m:
                tool_name = bracket_m.group(1)
                arg_value = bracket_m.group(2).strip()
                # Map bracket arg to the right parameter name
                _bracket_arg_map = {"search": "query", "fetch": "url"}
                arg_key = _bracket_arg_map.get(tool_name, "input")
                tool_args = {arg_key: arg_value}
                observation = None
            else:
                # No structured action — treat whole response as final
                return response.strip()

            if observation is None:
                observation = self._dispatch(tool_name, tool_args)

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
                    "timestamp": datetime.now(timezone.utc).isoformat(),
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
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
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
