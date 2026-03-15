"""Orchestrator — main loop, task routing, agent dispatch."""

from __future__ import annotations

import logging
from typing import Any, Callable

from orchid import config as cfg
from orchid.memory.state import Task, TaskStatus
from orchid.session import Session
from orchid.tools.models import call, route, Message

logger = logging.getLogger(__name__)

# Agent class registry
_AGENT_REGISTRY: dict[str, type] = {}


def _get_registry() -> dict[str, type]:
    if not _AGENT_REGISTRY:
        from orchid.agents.developer import DeveloperAgent
        from orchid.agents.researcher import ResearcherAgent
        from orchid.agents.reviewer import ReviewerAgent
        from orchid.agents.base import BaseAgent
        _AGENT_REGISTRY.update({
            "developer": DeveloperAgent,
            "researcher": ResearcherAgent,
            "reviewer": ReviewerAgent,
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
    """

    def __init__(self, session: Session, cli_model_override: str | None = None):
        self.session = session
        self.registry = _get_registry()
        self.cli_model_override = cli_model_override
        # Optional stream callback — set by BackgroundRunner for progress notifications
        self.stream_callback: Callable[[dict[str, Any]], None] | None = None
        # Delegation support — shared delegator across all tasks in this run
        from orchid.agents.delegator import AgentDelegator
        self._delegator = AgentDelegator(
            session=session,
            vector_memory=session._vector,
            project_name=session.project_name,
        )

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
        for i in range(max_tasks):
            result = self.run_once()
            if result is None:
                logger.info("All tasks complete after %d iterations.", i)
                break
            self.session.save()

    # ── Task execution ─────────────────────────────────────────────────────────

    def _execute_task(self, task: Task) -> dict[str, Any]:
        # Resolve routing before execution and log the decision
        decision = route(
            task_type=task.type,
            task_model_override=task.model_override,
            cli_override=self.cli_model_override,
            task_title=task.title,
        )
        logger.info(
            "Routing %s → %s (reason: %s, source: %s)",
            task.id, decision.model, decision.reason, decision.source,
        )

        logger.info("Executing task %s: %s [type=%s model=%s]", task.id, task.title, task.type, decision.model)
        self.session.update_task_status(task.id, TaskStatus.IN_PROGRESS)
        self.session.log_event("task_start", {
            "task_id": task.id,
            "title": task.title,
            "model": decision.model,
            "routing_reason": decision.reason,
            "routing_source": decision.source,
        })

        try:
            # Optionally plan/decompose complex tasks via Claude first
            if task.type in cfg.get("routing.claude_tasks", []):
                plan = self._plan_task(task)
                logger.info("Plan for %s:\n%s", task.id, plan[:500])
            else:
                plan = task.description or task.title

            # Dispatch to agent
            agent_cls = self._resolve_agent(task)
            injection_queue = self.session.project_dir / ".orchid" / "inject.queue"
            agent = agent_cls(
                session_context=self.session.context_block(),
                stream_callback=self._make_stream_callback(task.id),
                injection_queue_path=injection_queue,
            )
            # Override model_key based on routing decision
            agent.model_key = decision.model
            agent.delegator = self._delegator
            result_text = agent.run(plan)

            self.session.update_task_status(task.id, TaskStatus.DONE)
            delegation_count = len(self.session.delegations)
            self.session.log_event("task_done", {
                "task_id": task.id,
                "result": result_text[:500],
                "delegations": delegation_count,
            })

            # Append result summary to hot memory
            self._update_hot_memory(task, result_text)
            return {"task_id": task.id, "status": "done", "result": result_text}

        except Exception as e:
            logger.exception("Task %s failed: %s", task.id, e)
            self.session.update_task_status(task.id, TaskStatus.BLOCKED)
            self.session.log_event("task_error", {"task_id": task.id, "error": str(e)})
            return {"task_id": task.id, "status": "error", "error": str(e)}

    def _make_stream_callback(self, task_id: str) -> Callable[[dict[str, Any]], None] | None:
        """Build a stream callback that writes to live log and optionally fires progress notifications."""
        outer_stream = self.stream_callback
        session = self.session
        progress_interval = cfg.get("streaming.telegram_progress_interval", 3)

        def _cb(data: dict[str, Any]) -> None:
            # Write to live log
            session.stream_react(data)
            # Fire progress notification every N iterations
            if outer_stream is not None:
                iteration = data.get("iter", 0)
                if iteration > 0 and iteration % progress_interval == 0:
                    outer_stream({
                        "event": "task_progress",
                        "task_id": task_id,
                        "iter": iteration,
                        "thought_snippet": data.get("thought", "")[:80],
                    })

        return _cb

    def _plan_task(self, task: Task) -> str:
        """Use Claude to produce a step-by-step plan for complex tasks."""
        prompt = (
            f"You are orchestrating a software project. Break down this task into clear steps.\n\n"
            f"Task: {task.title}\n"
            f"Description: {task.description}\n\n"
            f"Project context:\n{self.session.context_block()[:1500]}\n\n"
            "Output a numbered list of concrete steps. Be brief."
        )
        return call(
            messages=[Message("user", prompt)],
            model_key="claude",
            system="You are a software project orchestrator.",
        )

    def _resolve_agent(self, task: Task):
        """Return the agent class for a task."""
        registry = _get_registry()
        if task.agent and task.agent in registry:
            return registry[task.agent]
        # Default mapping by task type
        type_map = {
            "code_generate": "developer",
            "draft": "developer",
            "search": "researcher",
            "summarize": "researcher",
            "review": "reviewer",
            "critique": "reviewer",
        }
        agent_name = type_map.get(task.type, "base")
        return registry.get(agent_name, registry["base"])

    def _update_hot_memory(self, task: Task, result: str) -> None:
        summary_line = f"\n- [{task.id}] {task.title}: {result[:200].strip()}\n"
        if "## Recent Completions" in self.session.hot_memory:
            self.session.hot_memory += summary_line
        else:
            self.session.hot_memory += "\n## Recent Completions\n" + summary_line
