"""Orchestrator — main loop, task routing, agent dispatch."""

from __future__ import annotations

import logging
from typing import Any

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

    def __init__(self, session: Session):
        self.session = session
        self.registry = _get_registry()

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
        logger.info("Executing task %s: %s [type=%s]", task.id, task.title, task.type)
        self.session.update_task_status(task.id, TaskStatus.IN_PROGRESS)
        self.session.log_event("task_start", {"task_id": task.id, "title": task.title})

        try:
            # Optionally plan/decompose complex tasks via Claude first
            if task.type in cfg.get("routing.claude_tasks", []):
                plan = self._plan_task(task)
                logger.info("Plan for %s:\n%s", task.id, plan[:500])
            else:
                plan = task.description or task.title

            # Dispatch to agent
            agent_cls = self._resolve_agent(task)
            agent = agent_cls(session_context=self.session.context_block())
            result_text = agent.run(plan)

            self.session.update_task_status(task.id, TaskStatus.DONE)
            self.session.log_event("task_done", {"task_id": task.id, "result": result_text[:500]})

            # Append result summary to hot memory
            self._update_hot_memory(task, result_text)
            return {"task_id": task.id, "status": "done", "result": result_text}

        except Exception as e:
            logger.exception("Task %s failed: %s", task.id, e)
            self.session.update_task_status(task.id, TaskStatus.BLOCKED)
            self.session.log_event("task_error", {"task_id": task.id, "error": str(e)})
            return {"task_id": task.id, "status": "error", "error": str(e)}

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
