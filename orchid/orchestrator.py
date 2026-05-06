"""Orchestrator — main loop, task routing, agent dispatch."""

from __future__ import annotations

import json as _json
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchid import config as cfg
from orchid.hooks.events import AGENT_ACTION, AGENT_OBSERVATION, TASK_COMPLETE, TASK_FAILED, TASK_START, HookEvent
from orchid.hooks.registry import HookRegistry
from orchid.memory.state import Task, TaskResultStore, TaskStatus
from orchid.output.emitter import EmitterProtocol, NullEmitter
from orchid.output.events import (
    AgentThoughtEvent,
    TaskCompleteEvent,
    TaskFailEvent,
    TaskStartEvent,
    ToolResultEvent,
    ToolUseEvent,
)
from orchid.session import Session
from orchid.tools.models import Message, RouteDecision, call

logger = logging.getLogger(__name__)

_TRUNC = 120




def _get_type_map() -> dict[str, str]:
    """Default agent type mapping by task type."""
    return {
        "code_generate": "developer",
        "draft": "developer",
        "search": "researcher",
        "summarize": "researcher",
        "review": "reviewer",
        "critique": "reviewer",
        "verify": "tester",
        "rollup": "base",
    }
class TraceWriter:
    """Writes ReAct iteration traces to <project>/.orchid/trace.log in append mode."""

    def __init__(self, project_dir: Path) -> None:
        self._path = project_dir / ".orchid" / "trace.log"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, text: str) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    @staticmethod
    def _trunc(s: str) -> str:
        return s[:_TRUNC] + " (truncated)" if len(s) > _TRUNC else s

    def task_start(self, task_id: str, title: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._write(f"\n[{ts}] Starting {task_id} — {title}")

    def iteration(
        self,
        task_id: str,
        iter_num: int,
        max_iter: int,
        elapsed: float,
        thought: str,
        action: str,
        action_input: str,
        observation: str,
    ) -> None:
        lines = [
            f"--- {task_id} iter {iter_num}/{max_iter} ({elapsed:.1f}s) ---",
            f"THOUGHT: {self._trunc(thought)}",
            f"ACTION:  {action}",
            f"INPUT:   {self._trunc(action_input)}",
            f"OBS:     {self._trunc(observation)}",
        ]
        self._write("\n".join(lines))

    def task_summary(
        self,
        task_id: str,
        status: str,
        completed_iters: int,
        max_iter: int,
        action_counts: dict[str, int],
        elapsed: float,
    ) -> None:
        counts = " ".join(
            f"{a}×{n}" for a, n in sorted(action_counts.items()) if n > 0
        ) or "no tools called"
        keyword = "DONE" if status == "done" else "BLOCKED"
        at_word = "in" if status == "done" else "at"
        self._write(
            f"=== {task_id} {keyword} {at_word} {completed_iters}/{max_iter} iters"
            f" | {counts} | {elapsed:.1f}s ==="
        )


# Agent class registry
_AGENT_REGISTRY: dict[str, type] = {}


def _get_registry() -> dict[str, type]:
    if not _AGENT_REGISTRY:
        from orchid.agents.base import BaseAgent
        from orchid.agents.developer import DeveloperAgent
        from orchid.agents.researcher import ResearcherAgent
        from orchid.agents.reviewer import ReviewerAgent
        from orchid.agents.tester import TesterAgent
        _AGENT_REGISTRY.update({
            "developer": DeveloperAgent,
            "researcher": ResearcherAgent,
            "reviewer": ReviewerAgent,
            "tester": TesterAgent,
            "base": BaseAgent,
        })
    return _AGENT_REGISTRY


class Orchestrator:
    """
    Top-level loop that:
    1. Loads session state
    2. Picks the next task
    3. Plans / decomposes if needed (via Claude)
    4. Dispatches to the appropriate agent
    5. Records results and updates state
    6. Saves and closes the session

    T096: Hooks are wired into the task lifecycle at the following points:
    - task_start: Before task execution begins
    - task_complete: After successful task completion
    - task_failed: When task fails or is blocked
    - agent_action/observation: During ReAct loop iterations

    T097: Hooks are also wired into session and phase transitions via:
    - Session._fire_session_start_hook() / _fire_session_end_hook()
    - ProjectLifecycle._fire_phase_transition_hook() etc.
    """

    def __init__(
        self,
        session: Session,
        cli_model_override: str | None = None,
        cli_provider_overrides: dict[str, str] | None = None,
        offline_mode: bool = False,
        trace_enabled: bool = False,
        stream_emitter: EmitterProtocol | None = None,
    ):
        self.session = session
        self.registry = _get_registry()
        self._trace_writer: TraceWriter | None = (
            TraceWriter(session.project_dir) if trace_enabled else None
        )
        self.cli_model_override = cli_model_override
        self.cli_provider_overrides: dict[str, str] = cli_provider_overrides or {}
        self.offline_mode = offline_mode
        # Optional stream emitter for typed events
        self._stream_emitter: EmitterProtocol = (
            stream_emitter if stream_emitter is not None else NullEmitter()
        )
        # Apply offline mode to provider registry
        if offline_mode:
            from orchid.providers.registry import get_registry as get_provider_registry
            get_provider_registry().set_offline(True)
        # Optional stream callback — set by BackgroundRunner for progress notifications
        self.stream_callback: Callable[[dict[str, Any]], None] | None = None
        # Delegation support — shared delegator across all tasks in this run
        from orchid.agents.delegator import AgentDelegator
        self._delegator = AgentDelegator(
            session=session,
            vector_memory=session._vector,
            project_name=session.project_name,
        )
        # Hook registry for task events (T096)
        self._hook_registry = HookRegistry()
        self._load_hooks()
        # T113: MCP manager — created once, connected lazily on first task
        self._mcp_manager: Any = None
        # T199: Agent pool for reusable agent instances
        self._agent_pool: Any = None

    def _load_hooks(self) -> None:
        """Load and register hooks from project configuration."""
        try:
            from orchid.hooks.loader import HookLoader
            loader = HookLoader(self.session.project_dir)
            count = loader.load()
            # Merge loaded hooks into this orchestrator's registry
            if loader.registry:
                for event_type, handlers in loader.registry._handlers.items():
                    for handler in handlers:
                        self._hook_registry._handlers[event_type].append(handler)
                logger.info("Loaded %d hook(s) for orchestrator", count)
        except Exception as e:
            logger.warning("Failed to load hooks: %s", e)

    # ── Public entry points ────────────────────────────────────────────────────

    def run_once(self) -> dict[str, Any] | None:
        """Pick and execute one task. Returns result dict or None if no tasks."""
        task = self.session.next_task()
        if task is None:
            logger.info("No tasks to run.")
            return None
        return self._execute_task(task)

    def run_loop(self, max_tasks: int = 100) -> None:
        """Run tasks until none remain or max_tasks reached."""
        auto_review_enabled = cfg.get("auto_review.enabled", False)
        auto_review_after = cfg.get("auto_review.after_n_tasks", 3)
        auto_verify_enabled = cfg.get("auto_verify", False)
        code_gen_since_review = 0

        for i in range(max_tasks):
            task = self.session.next_task()
            if task is None:
                logger.info("All tasks complete after %d iterations.", i)
                break
            result = self._execute_task(task)
            self.session.save()

            # T043: auto-insert review task after N completed code_generate tasks
            if (
                auto_review_enabled
                and result.get("status") == "done"
                and task.type == "code_generate"
            ):
                code_gen_since_review += 1
                if code_gen_since_review >= auto_review_after:
                    self._insert_auto_review_task()
                    code_gen_since_review = 0

            # T083: auto-inject verify task after completed code_generate tasks
            if (
                auto_verify_enabled
                and result.get("status") == "done"
                and task.type == "code_generate"
            ):
                files = result.get("files_written", [])
                self._insert_auto_verify_task(task, files)

    # ── Task execution ─────────────────────────────────────────────────────────


    def _resolve_provider(self, task: Task) -> RouteDecision:
        """Resolve the provider/model for a task via the full routing chain.

        Returns a RouteDecision with model, reason, and source fields.
        """
        # Resolve agent type for per-agent-type provider overrides
        agent_cls = self._resolve_agent(task)
        agent_type = getattr(agent_cls, "agent_type", "base")

        # Check per-agent-type CLI override first
        per_agent_override = self.cli_provider_overrides.get(agent_type)
        _agent_name = getattr(agent_cls, "agent_name", agent_type)

        # Resolve provider through the full registry chain
        from orchid.providers.registry import get_registry as _get_provider_registry
        _provider_name = _get_provider_registry().resolve_name(
            agent_type=agent_type,
            agent_name=_agent_name,
            task_type=task.type,
            task_model=task.model_override,
            cli_override=per_agent_override or self.cli_model_override,
            task_title=task.title,
        )
        decision = RouteDecision(model=_provider_name, reason="registry", source="registry")

        # Offline mode forces local regardless of routing
        if self.offline_mode:
            decision = RouteDecision(model="local", reason="offline mode", source="cli_flag")

        logger.info(
            "Routing %s -> %s (reason: %s, source: %s)",
            task.id, decision.model, decision.reason, decision.source,
        )
        return decision

    def _execute_task(self, task: Task) -> dict[str, Any]:
        from orchid.providers.base import ProviderUnavailableError

        # Re-assert project config before every task
        cfg.configure_for_project(self.session.project_dir)

        # T188b: Register this session as the active session so providers
        # (e.g. LocalProvider) can retrieve session stats via
        # get_current_session() during task execution.
        self.session.set_active_session()
        # Also wire into task_injection thread-local so spawn_task works in agents.
        from orchid.tools.task_injection import set_active_session as _set_ti_session
        _set_ti_session(self.session)

        # Resolve provider via extracted method
        decision = self._resolve_provider(task)

        # T141: Capture checkpoint before task execution for rewind/resume support
        try:
            from orchid.checkpoint.store import CheckpointStore
            _checkpoint_store = CheckpointStore(self.session.project_dir)
            _checkpoint_store.save(
                tasks=[t.to_dict() for t in self.session.tasks],
                hot_memory=self.session.hot_memory,
                decisions=self.session.decisions,
                delegations=self.session.delegations,
                task_id=task.id,
                description=f"Pre-execution checkpoint for {task.id}",
            )
        except Exception as _cp_exc:  # noqa: BLE001
            logger.warning("Checkpoint capture failed for %s: %s", task.id, _cp_exc)

        self.session.update_task_status(task.id, TaskStatus.IN_PROGRESS)
        # Persist IN_PROGRESS immediately so web UI shows correct status during execution
        from orchid.memory.state import save_tasks
        save_tasks(self.session.tasks, self.session.project_dir)
        self.session.log_event("task_start", {
            "task_id": task.id,
            "title": task.title,
            "model": decision.model,
            "routing_reason": decision.reason,
            "routing_source": decision.source,
        })

        # T096: Fire task_start hook
        self._fire_task_start_hook(task, decision.model)

        # Emit typed TaskStartEvent via stream emitter
        self._stream_emitter.emit(TaskStartEvent(
            session_id=getattr(self.session, "_log_path", None) and str(self.session._log_path.stem) or self.session.project_name,
            task_id=task.id,
            task_title=task.title,
            task_type=task.type,
        ))

        try:
            # Rollup tasks are synthesised by the orchestrator directly — no agent dispatch
            if task.type == "rollup":
                return self._execute_rollup_task(task)

            # Optionally plan/decompose complex tasks via Claude first
            if not self.offline_mode and task.type in cfg.get("routing.claude_tasks", []):
                plan = self._plan_task(task)
                logger.info("Plan for %s:\n%s", task.id, plan[:500])
            else:
                plan = task.description or task.title

            # T044: inject project context (language, module system, framework)
            session_context = self.session.context_block()
            try:
                from orchid.tools.project_context import ProjectContextTool
                ctx_block = ProjectContextTool(str(self.session.project_dir)).project_context()
                session_context = session_context + "\n\n" + ctx_block
            except Exception as _pce:
                logger.debug("project_context detection failed: %s", _pce)

            # T045: wrap write_file to track files written during this task
            files_written: list[str] = []

            # T113: Wire MCP tools into the agent — connect manager lazily on first task
            self._ensure_mcp_connected()

            # T199: Dispatch to agent via pool
            injection_queue = self.session.project_dir / ".orchid" / "inject.queue"
            stream_cb = self._make_stream_callback(task.id, task.title)
            agent_type = task.agent or _get_type_map().get(task.type, "base")
            agent = self._get_agent(
                agent_type=agent_type,
                decision_model=decision.model,
                session_context=session_context,
                stream_callback=stream_cb,
                injection_queue_path=injection_queue,
            )
            agent.model_key = decision.model
            agent.delegator = self._delegator

            # T113: Inject MCP tools into the agent's tool registry
            if self._mcp_manager is not None:
                mcp_tools = self._mcp_manager.list_tools()
                for tool in mcp_tools:
                    def _make_mcp_tool_fn(adapter, tool_name: str) -> Callable[..., str]:
                        def _fn(**kwargs: Any) -> str:
                            result = adapter.call_tool(tool_name, kwargs)
                            return str(result.content) if result else ""
                        return _fn
                    fn = _make_mcp_tool_fn(self._mcp_manager.get_adapter(tool.server_name) or self._mcp_manager, tool.name)
                    agent.tools[tool.name] = fn

            # Wrap write_file to record paths (T045)
            _orig_write = agent.tools.get("write_file")
            if _orig_write:
                def _tracking_write(path: str, content: str = "") -> str:
                    if not content:
                        return (
                            "[write_file error: content is required — use the heredoc format:\n"
                            "Action: write_file\n"
                            "Action Path: <path>\n"
                            "Action Content:\n"
                            "<<<ORCHID\n"
                            "<file content>\n"
                            "ORCHID]"
                        )
                    result = _orig_write(path=path, content=content)
                    if not result.startswith("["):
                        files_written.append(path)
                    return result
                agent.tools["write_file"] = _tracking_write

            _task_run_start = time.monotonic()
            result_text = agent.run(plan)
            _task_run_elapsed = time.monotonic() - _task_run_start

            # Detect failure sentinels returned by the agent run loop
            _FAILURE_PREFIXES = (
                "[max iterations reached",
                "[parse error",
                "[tool error",
                "[path error",
                "[delegation refused",
                "[unknown tool",
            )
            is_failure = result_text.startswith(_FAILURE_PREFIXES)

            # Extract trace state (available regardless of trace_enabled)
            _ts = getattr(stream_cb, "_trace_state", {}) if stream_cb is not None else {}

            # Write trace summary
            if self._trace_writer:
                self._trace_writer.task_summary(
                    task_id=task.id,
                    status="blocked" if is_failure else "done",
                    completed_iters=_ts.get("completed_iters", 0),
                    max_iter=cfg.get("agents.max_react_iterations", 25),
                    action_counts=dict(_ts.get("action_counts", {})),
                    elapsed=_task_run_elapsed,
                )

            delegation_count = len(self.session.delegations)
            if is_failure:
                logger.warning("Task %s failed: %s", task.id, result_text[:200])
                self.session.update_task_status(task.id, TaskStatus.BLOCKED)
                self.session.log_event("task_failed", {
                    "task_id": task.id,
                    "reason": result_text[:500],
                    "delegations": delegation_count,
                })
                self._update_hot_memory(task, f"FAILED: {result_text}")
                self._write_task_metrics(
                    task=task,
                    status="blocked",
                    trace_state=_ts,
                    elapsed=_task_run_elapsed,
                    model=decision.model,
                    result_text=result_text,
                )
                # T096: Fire task_failed hook
                self._fire_task_failed_hook(task, result_text)
                # Emit typed TaskFailEvent via stream emitter
                self._stream_emitter.emit(TaskFailEvent(
                    session_id=getattr(self.session, "_log_path", None) and str(self.session._log_path.stem) or self.session.project_name,
                    task_id=task.id,
                    error=result_text[:500],
                ))
                return {"task_id": task.id, "status": "failed", "error": result_text}
            else:
                self.session.update_task_status(task.id, TaskStatus.DONE)
                self.session.log_event("task_done", {
                    "task_id": task.id,
                    "result": result_text[:500],
                    "delegations": delegation_count,
                })
                # T045: log file manifest
                if files_written:
                    self.session.log_event("task_manifest", {
                        "task_id": task.id,
                        "files_created": files_written,
                        "files_modified": [],
                    })
                # Store result for future rollup tasks
                TaskResultStore(self.session.project_dir).append(
                    task.id, task.title, task.type, result_text
                )
                self._update_hot_memory(task, result_text)
                self._write_task_metrics(
                    task=task,
                    status="done",
                    trace_state=_ts,
                    elapsed=_task_run_elapsed,
                    model=decision.model,
                )
                # T096: Fire task_complete hook
                self._fire_task_complete_hook(task, result_text, files_written)
                # Emit typed TaskCompleteEvent via stream emitter
                self._stream_emitter.emit(TaskCompleteEvent(
                    session_id=getattr(self.session, "_log_path", None) and str(self.session._log_path.stem) or self.session.project_name,
                    task_id=task.id,
                    duration_s=_task_run_elapsed,
                    iterations=_ts.get("completed_iters", 0),
                ))
                return {"task_id": task.id, "status": "done", "result": result_text, "files_written": files_written}

        except ProviderUnavailableError as e:
            logger.error("Provider unavailable for task %s: %s — %s", task.id, e.missing, e.suggestion)
            self.session.update_task_status(task.id, TaskStatus.BLOCKED)
            error_msg = f"Provider '{e.provider_name}' unavailable: {e.missing}"
            self.session.log_event("task_error", {"task_id": task.id, "error": error_msg, "fix": e.suggestion})
            if self.stream_callback:
                self.stream_callback({
                    "event": "provider_unavailable",
                    "provider": e.provider_name,
                    "missing": e.missing,
                    "fix": e.suggestion,
                })
            # T096: Fire task_failed hook
            self._fire_task_failed_hook(task, error_msg)
            # Emit typed TaskFailEvent via stream emitter
            self._stream_emitter.emit(TaskFailEvent(
                session_id=getattr(self.session, "_log_path", None) and str(self.session._log_path.stem) or self.session.project_name,
                task_id=task.id,
                error=error_msg,
            ))
            return {"task_id": task.id, "status": "error", "error": error_msg}

        except Exception as e:
            logger.exception("Task %s failed: %s", task.id, e)
            self.session.update_task_status(task.id, TaskStatus.BLOCKED)
            self.session.log_event("task_error", {"task_id": task.id, "error": str(e)})
            # T096: Fire task_failed hook
            self._fire_task_failed_hook(task, str(e))
            # Emit typed TaskFailEvent via stream emitter
            self._stream_emitter.emit(TaskFailEvent(
                session_id=getattr(self.session, "_log_path", None) and str(self.session._log_path.stem) or self.session.project_name,
                task_id=task.id,
                error=str(e)[:500],
            ))
            return {"task_id": task.id, "status": "error", "error": str(e)}


    def _get_agent(
        self,
        agent_type: str,
        decision_model: str,
        session_context: str,
        stream_callback: Callable | None,
        injection_queue_path: Path,
    ) -> Any:
        """Get an agent instance — from pool if available, else direct creation.

        T199: Uses the global AgentPool for agent reuse. Falls back to direct
        instantiation if the pool is unavailable or fails.
        """
        try:
            if self._agent_pool is None:
                from orchid.agent_pool import get_agent_pool
                self._agent_pool = get_agent_pool()
            agent = self._agent_pool.acquire(
                agent_type=agent_type,
                model_key=decision_model,
                project_dir=self.session.project_dir,
                session_context=session_context,
                stream_callback=stream_callback,
                injection_queue_path=injection_queue_path,
            )
            logger.debug(
                "[orchestrator] agent acquired from pool: %s (model=%s)",
                agent_type, decision_model,
            )
            return agent
        except Exception as e:
            logger.debug(
                "[orchestrator] pool acquire failed for %s: %s — falling back to direct creation",
                agent_type, e,
            )

        # Fallback: create directly
        agent_cls = self._resolve_agent_for_type(agent_type)
        if agent_type.lower().strip() == "researcher":
            from orchid.session import get_current_session
            session = get_current_session()
            vector_memory = session._vector if session else None
            agent = agent_cls(
                session_context=session_context,
                vector_memory=vector_memory,
                project_name=session.project_name if session else "",
                project_dir=self.session.project_dir,
                stream_callback=stream_callback,
                injection_queue_path=injection_queue_path,
            )
        else:
            agent = agent_cls(
                session_context=session_context,
                project_dir=self.session.project_dir,
                stream_callback=stream_callback,
                injection_queue_path=injection_queue_path,
            )

        agent.model_key = decision_model
        logger.debug(
            "[orchestrator] agent created directly: %s (model=%s)",
            agent_type, decision_model,
        )
        return agent

    def _resolve_agent_for_type(self, agent_type: str) -> type:
        """Resolve agent class from agent_type string."""
        registry = _get_registry()
        return registry.get(agent_type.lower().strip(), registry["base"])

    # T096: Task lifecycle hook methods
    def _fire_task_start_hook(self, task: Task, model: str) -> None:
        """Fire the task_start hook event."""
        event = HookEvent(
            event_type=TASK_START,
            data={
                "task_id": task.id,
                "title": task.title,
                "type": task.type,
                "priority": task.priority,
                "model": model,
            },
            context={"task_id": task.id},
        )
        self._hook_registry.fire(event)

    def _fire_task_complete_hook(
        self, task: Task, result: str, files_written: list[str]
    ) -> None:
        """Fire the task_complete hook event."""
        event = HookEvent(
            event_type=TASK_COMPLETE,
            data={
                "task_id": task.id,
                "title": task.title,
                "type": task.type,
                "result": result[:1000],  # Truncate for hooks
                "files_written": files_written,
            },
            context={"task_id": task.id},
        )
        self._hook_registry.fire(event)

    def _fire_task_failed_hook(self, task: Task, error: str) -> None:
        """Fire the task_failed hook event."""
        event = HookEvent(
            event_type=TASK_FAILED,
            data={
                "task_id": task.id,
                "title": task.title,
                "type": task.type,
                "error": error[:1000],  # Truncate for hooks
            },
            context={"task_id": task.id},
        )
        self._hook_registry.fire(event)

    def _make_stream_callback(
        self, task_id: str, task_title: str = ""
    ) -> Callable[[dict[str, Any]], None] | None:
        """Build a stream callback that writes to live log and optionally fires progress notifications."""
        outer_stream = self.stream_callback
        session = self.session
        progress_interval = cfg.get("streaming.telegram_progress_interval", 3)
        trace = self._trace_writer
        max_iter = cfg.get("agents.max_react_iterations", 25)

        # Mutable state for trace bookkeeping and metrics
        _state: dict[str, Any] = {
            "completed_iters": 0,
            "action_counts": defaultdict(int),
            "iter_start": time.monotonic(),
            "last_action": "",
            "last_error": "",
        }

        if trace:
            trace.task_start(task_id, task_title)

        def _cb(data: dict[str, Any]) -> None:
            # Write to live log
            session.stream_react(data)

            iteration = data.get("iter", 0)
            _state["completed_iters"] = iteration + 1

            # Always track action counts, last action, and last error for metrics
            action = data.get("action", "")
            if action and action != "final_answer":
                _state["action_counts"][action] += 1
                _state["last_action"] = action
            observation = data.get("observation", "")
            if observation and observation.startswith("["):
                _state["last_error"] = observation[:300]

            # T096: Fire agent action/observation hooks
            if action and action != "final_answer":
                action_event = HookEvent(
                    event_type=AGENT_ACTION,
                    data={
                        "task_id": task_id,
                        "action": action,
                        "input": data.get("action_input", ""),
                        "iteration": iteration,
                    },
                    context={"task_id": task_id, "action": action},
                )
                self._hook_registry.fire(action_event)

            if observation:
                is_error = observation.startswith("[")
                obs_event = HookEvent(
                    event_type=AGENT_OBSERVATION,
                    data={
                        "task_id": task_id,
                        "action": action,
                        "observation": observation,
                        "error": is_error,
                    },
                    context={"task_id": task_id, "action": action},
                )
                self._hook_registry.fire(obs_event)

            # Write trace line
            if trace:
                now = time.monotonic()
                elapsed = now - _state["iter_start"]
                _state["iter_start"] = now
                trace.iteration(
                    task_id=task_id,
                    iter_num=iteration + 1,
                    max_iter=max_iter,
                    elapsed=elapsed,
                    thought=data.get("thought", ""),
                    action=action,
                    action_input=data.get("action_input", ""),
                    observation=observation,
                )

            # Emit typed events via stream emitter
            thought = data.get("thought", "")
            if thought and iteration == 0:
                self._stream_emitter.emit(AgentThoughtEvent(
                    session_id=getattr(session, "_log_path", None) and str(session._log_path.stem) or session.project_name,
                    task_id=task_id,
                    thought=thought[:500],
                ))

            if action and action != "final_answer":
                self._stream_emitter.emit(ToolUseEvent(
                    session_id=getattr(session, "_log_path", None) and str(session._log_path.stem) or session.project_name,
                    task_id=task_id,
                    tool=action,
                    input_summary=data.get("action_input", "")[:300],
                ))

            if observation:
                self._stream_emitter.emit(ToolResultEvent(
                    session_id=getattr(session, "_log_path", None) and str(session._log_path.stem) or session.project_name,
                    task_id=task_id,
                    tool=action,
                    output_summary=observation[:300],
                ))

            # Fire progress notification every N iterations
            if outer_stream is not None:
                if iteration > 0 and iteration % progress_interval == 0:
                    outer_stream({
                        "event": "task_progress",
                        "task_id": task_id,
                        "iter": iteration,
                        "thought_snippet": data.get("thought", "")[:80],
                    })

        _cb._trace_state = _state  # type: ignore[attr-defined]
        return _cb

    def _ensure_mcp_connected(self) -> None:
        """T113: Lazily create and connect the MCP manager on first task execution."""
        if self._mcp_manager is not None:
            return
        from orchid.mcp.manager import MCPManager
        self._mcp_manager = MCPManager()
        try:
            self._mcp_manager.connect()
            logger.info("MCP servers connected: %s", list(self._mcp_manager._adapters.keys()))
        except Exception as exc:
            logger.warning("MCP connection failed (MCP tools unavailable): %s", exc)
            self._mcp_manager = None

    def _plan_task(self, task: Task) -> str:
        """Produce a step-by-step plan for complex tasks via the provider registry."""
        prompt = (
            f"You are orchestrating a software project. Break down this task into clear steps.\n\n"
            f"Task: {task.title}\n"
            f"Description: {task.description}\n\n"
            f"Project context:\n{self.session.context_block()[:1500]}\n\n"
            "Output a numbered list of concrete steps. Be brief."
        )
        from orchid.providers.registry import get_registry as get_provider_registry
        model_key = get_provider_registry().resolve_name(
            agent_type="orchestrator",
            cli_override=self.cli_provider_overrides.get("orchestrator") or self.cli_model_override,
        )
        return call(
            messages=[Message("user", prompt)],
            model_key=model_key,
            system="You are a software project orchestrator.",
        )

    def _resolve_agent(self, task: Task):
        """Return the agent class for a task."""
        registry = _get_registry()
        if task.agent and task.agent in registry:
            return registry[task.agent]
        # Default mapping by task type
        agent_name = _get_type_map().get(task.type, "base")
        return registry.get(agent_name, registry["base"])

    def _execute_rollup_task(self, task: Task) -> dict[str, Any]:
        """Synthesise results from multiple completed tasks into a summary document."""
        from orchid.memory.state import save_tasks

        max_sources = cfg.get("rollup.max_sources", 20)
        sources = task.rollup_sources[:max_sources]

        if not sources:
            msg = f"Rollup {task.id} has no rollup_sources — add `rollup:T001,T002` annotation"
            logger.warning(msg)
            self.session.update_task_status(task.id, TaskStatus.BLOCKED)
            save_tasks(self.session.tasks, self.session.project_dir)
            self.session.log_event("task_failed", {"task_id": task.id, "reason": msg})
            # T096: Fire task_failed hook
            self._fire_task_failed_hook(task, msg)
            # Emit typed TaskFailEvent via stream emitter
            self._stream_emitter.emit(TaskFailEvent(
                session_id=getattr(self.session, "_log_path", None) and str(self.session._log_path.stem) or self.session.project_name,
                task_id=task.id,
                error=msg,
            ))
            return {"task_id": task.id, "status": "failed", "error": msg}

        # Check all sources are DONE
        completed_ids = {t.id for t in self.session.tasks if t.status == TaskStatus.DONE}
        incomplete = [s for s in sources if s not in completed_ids]
        if incomplete:
            msg = f"Rollup {task.id} blocked — waiting for {', '.join(incomplete)}"
            logger.info(msg)
            self.session.update_task_status(task.id, TaskStatus.BLOCKED)
            save_tasks(self.session.tasks, self.session.project_dir)
            self.session.log_event("task_failed", {"task_id": task.id, "reason": msg})
            # T096: Fire task_failed hook
            self._fire_task_failed_hook(task, msg)
            # Emit typed TaskFailEvent via stream emitter
            self._stream_emitter.emit(TaskFailEvent(
                session_id=getattr(self.session, "_log_path", None) and str(self.session._log_path.stem) or self.session.project_name,
                task_id=task.id,
                error=msg,
            ))
            return {"task_id": task.id, "status": "blocked", "error": msg}

        # Gather stored results
        store = TaskResultStore(self.session.project_dir)
        results = store.get_many(sources)

        results_text = ""
        for entry in results:
            tid = entry.get("task_id", "?")
            title = entry.get("title", "")
            result = entry.get("result", "(no result)")
            results_text += f"\n[{tid}] {title}:\n{result}\n"

        stored_ids = {e.get("task_id") for e in results}
        missing_results = [s for s in sources if s not in stored_ids]
        if missing_results:
            results_text += f"\n[Note: no stored results found for: {', '.join(missing_results)}]\n"

        prompt = (
            "You are synthesizing results from multiple review/analysis tasks.\n"
            "Produce a clear, concise summary document covering:\n"
            "- Overall status (passing/failing)\n"
            "- Critical issues found\n"
            "- Items verified as passing\n"
            "- Recommended next steps\n\n"
            f"Task results:\n{results_text}"
        )

        from orchid.providers.registry import get_registry as get_provider_registry
        model_key = get_provider_registry().resolve_name(
            agent_type="orchestrator",
            task_type="rollup",
            task_model=task.model_override,
            cli_override=self.cli_model_override,
        )
        logger.info("Running rollup synthesis for %s with %d source tasks", task.id, len(sources))
        synthesis = call(
            messages=[Message("user", prompt)],
            model_key=model_key,
            system="You are a technical synthesis assistant. Produce clear, structured summaries.",
        )

        # Determine output file path
        default_template = cfg.get("rollup.default_output", "ROLLUP-{task_id}.md")
        output_filename = task.output_file or default_template.replace("{task_id}", task.id)
        output_path = self.session.project_dir / output_filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(synthesis, encoding="utf-8")

        result_msg = f"Rollup written to {output_filename}"
        self.session.update_task_status(task.id, TaskStatus.DONE)
        save_tasks(self.session.tasks, self.session.project_dir)
        self.session.log_event("task_done", {
            "task_id": task.id,
            "result": result_msg,
            "output_file": output_filename,
        })
        store.append(task.id, task.title, task.type, result_msg)
        self._update_hot_memory(task, result_msg)
        # T096: Fire task_complete hook
        self._fire_task_complete_hook(task, result_msg, [output_filename])
        # Emit typed TaskCompleteEvent via stream emitter
        self._stream_emitter.emit(TaskCompleteEvent(
            session_id=getattr(self.session, "_log_path", None) and str(self.session._log_path.stem) or self.session.project_name,
            task_id=task.id,
            duration_s=0.0,
            iterations=0,
        ))

        return {
            "task_id": task.id,
            "status": "done",
            "result": result_msg,
            "output_file": output_filename,
        }

    def _insert_auto_review_task(self) -> None:
        """Insert an auto-review task after N code_generate completions (T043)."""
        from datetime import datetime
        ts = datetime.now(UTC).strftime("%H%M%S")
        review_task = Task(
            id=f"AUTOREV_{ts}",
            title="Auto-review: check imports and syntax verification",
            type="review",
            priority=1,
            description=(
                "Run check_imports on the project directory to verify all relative imports "
                "resolve correctly. Report any broken imports or syntax errors."
            ),
        )
        self.session.tasks.append(review_task)
        logger.info("Auto-review task inserted: %s", review_task.id)

    def _insert_auto_verify_task(self, source_task: Task, files_written: list[str]) -> None:
        """T083: Insert a verify task immediately after a completed code_generate task."""
        from datetime import datetime as _dt
        ts = _dt.now(UTC).strftime("%H%M%S")
        files_str = ", ".join(files_written) if files_written else "written files"
        verify_task = Task(
            id=f"{source_task.id}-verify-{ts}",
            title=f"Verify {source_task.id}: {files_str}"[:100],
            type="verify",
            priority=source_task.priority,
            description=(
                f"Verify the output of task {source_task.id}: {source_task.title}.\n"
                f"Files to check: {files_str}\n"
                "Run the project test suite and report results."
            ),
            depends_on=[source_task.id],
        )
        # Insert at front of pending tasks (after any already in-progress)
        self.session.tasks.insert(0, verify_task)
        logger.info("Auto-verify task inserted: %s targeting %s", verify_task.id, files_str[:80])

    def _update_hot_memory(self, task: Task, result: str) -> None:
        summary_line = f"\n- [{task.id}] {task.title}: {result[:200].strip()}\n"
        if "## Recent Completions" in self.session.hot_memory:
            self.session.hot_memory += summary_line
        else:
            self.session.hot_memory += "\n## Recent Completions\n" + summary_line

    def _write_task_metrics(
        self,
        task: Task,
        status: str,
        trace_state: dict[str, Any],
        elapsed: float,
        model: str,
        result_text: str = "",
    ) -> None:
        """Write a structured metrics record to .orchid/task_metrics.jsonl (T085)."""
        metrics_path = self.session.project_dir / ".orchid" / "task_metrics.jsonl"
        metrics_path.parent.mkdir(parents=True, exist_ok=True)

        session_id = ""
        if self.session._log_path:
            session_id = Path(self.session._log_path).stem

        record: dict[str, Any] = {
            "task_id": task.id,
            "title": task.title,
            "status": status,
            "iters_used": trace_state.get("completed_iters", 0),
            "iters_max": cfg.get("agents.max_react_iterations", 25),
            "duration_s": round(elapsed, 3),
            "action_counts": dict(trace_state.get("action_counts", {})),
            "model": model,
            "session_id": session_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        if status == "blocked":
            record["blocker"] = {
                "reason": result_text[:500],
                "last_action": trace_state.get("last_action", ""),
                "last_error": trace_state.get("last_error", ""),
            }

        try:
            with metrics_path.open("a", encoding="utf-8") as fh:
                fh.write(_json.dumps(record) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write task metrics for %s: %s", task.id, exc)