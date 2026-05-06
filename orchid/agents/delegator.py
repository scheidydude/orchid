"""Agent delegator — instantiates and runs sub-agents for focused sub-tasks."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from orchid import config as cfg

if TYPE_CHECKING:
    from orchid.memory.vector import VectorMemory
    from orchid.session import Session

logger = logging.getLogger(__name__)


def _get_agent_class(agent_type: str):
    """Map agent_type string → agent class. Raises ValueError for unknown types."""
    from orchid.agents.base import BaseAgent
    from orchid.agents.developer import DeveloperAgent
    from orchid.agents.researcher import ResearcherAgent
    from orchid.agents.reviewer import ReviewerAgent

    _map = {
        "developer": DeveloperAgent,
        "researcher": ResearcherAgent,
        "reviewer": ReviewerAgent,
        "base": BaseAgent,
    }
    key = agent_type.lower().strip()
    cls = _map.get(key)
    if cls is None:
        raise ValueError(f"Unknown agent type: {agent_type!r}. Valid: {sorted(_map)}")
    return cls


class AgentDelegator:
    """
    Handles agent-to-agent delegation within the ReAct loop.

    Instantiated by the Orchestrator and injected into agents. Agents call
    self.delegator.delegate() when they emit 'Action: delegate[agent | task]'.
    """

    def __init__(
        self,
        session: Session | None = None,
        vector_memory: VectorMemory | None = None,
        project_name: str = "",
    ):
        self.session = session
        self._vector = vector_memory
        self.project_name = project_name

    def delegate(
        self,
        agent_type: str,
        task: str,
        context: str,
        depth: int = 0,
        parent_agent: str = "unknown",
    ) -> str:
        """
        Spawn a sub-agent, run it on task, and return its final answer.

        depth: current delegation depth (0 = called from a top-level agent).
        Returns error strings on failure rather than raising.
        """
        max_depth = cfg.get("delegation.max_depth", 3)
        if depth >= max_depth:
            logger.warning("[delegator] depth limit reached (%d/%d)", depth, max_depth)
            return f"[delegation refused: max depth {max_depth} reached]"

        if not cfg.get("delegation.enabled", True):
            return "[delegation disabled by config]"

        logger.info(
            "[delegator] depth=%d %s → %s: %s",
            depth, parent_agent, agent_type, task[:80],
        )

        try:
            agent_cls = _get_agent_class(agent_type)
        except ValueError as e:
            return f"[delegation error: {e}]"

        sub_context = self._build_sub_context(task, context, depth)
        max_iter = cfg.get("delegation.max_sub_iterations", 5)

        project_dir = self.session.project_dir if self.session else None

        # ── Worktree integration ───────────────────────────────────────────────
        wt_path = None
        task_id = ""
        worktree_enabled = cfg.get("worktree.enabled", False)
        if worktree_enabled and project_dir:
            from orchid.worktree import WorktreeManager

            try:
                manager = WorktreeManager(project_dir)
                # Extract task_id from task string if present (e.g. "T170: Create file")
                task_id = self._extract_task_id(task)
                if task_id:
                    wt_path = manager.create(task_id, agent_type)
                    logger.info(
                        "[delegator] worktree created: branch=%s path=%s task=%s",
                        manager.get_worktree_branch(task_id),
                        wt_path,
                        task_id,
                    )
            except Exception as e:
                logger.warning("[delegator] worktree creation failed: %s", e)
                wt_path = None

        # Researcher requires extra kwargs for web search tooling
        if agent_type.lower().strip() == "researcher":
            agent = agent_cls(
                session_context=sub_context,
                vector_memory=self._vector,
                project_name=self.project_name,
                project_dir=wt_path or project_dir,
            )
        else:
            agent = agent_cls(
                session_context=sub_context,
                project_dir=wt_path or project_dir,
            )

        # Wire delegation into sub-agent so it can further delegate (at depth+1)
        sub_delegator = AgentDelegator(
            session=self.session,
            vector_memory=self._vector,
            project_name=self.project_name,
        )
        agent.delegator = sub_delegator
        agent.delegation_depth = depth + 1
        agent.max_iterations = max_iter

        result = agent.run(task)
        result_summary = result[:500]

        # ── Post-run worktree operations ───────────────────────────────────────
        if wt_path and task_id:
            try:
                from orchid.worktree import WorktreeManager

                manager = WorktreeManager(project_dir)
                # Commit any changes made by the sub-agent
                commit_msg = cfg.get("worktree.commit_message", "worktree: task completion")
                commit_result = manager.commit_worktree(task_id, commit_msg)
                logger.info("[delegator] worktree commit: %s", commit_result)

                # Optionally remove the worktree after completion
                auto_remove = cfg.get("worktree.auto_remove", True)
                if auto_remove:
                    remove_result = manager.remove(task_id)
                    logger.info("[delegator] worktree removed: %s", remove_result)
            except Exception as e:
                logger.warning("[delegator] worktree post-processing failed: %s", e)

        timestamp = datetime.now(UTC).isoformat()
        delegation_record: dict[str, Any] = {
            "session_id": (
                self.session._log_path.stem
                if self.session and self.session._log_path
                else ""
            ),
            "parent_agent": parent_agent,
            "child_agent": agent_type,
            "task": task,
            "result_summary": result_summary,
            "depth": depth,
            "timestamp": timestamp,
        }

        if self.session and cfg.get("delegation.log_delegations", True):
            self.session.record_delegation(delegation_record)

        if cfg.get("delegation.embed_results", True) and self._vector and self._vector.available:
            try:
                ts_int = int(datetime.now(UTC).timestamp())
                self._vector.add(
                    text=f"Delegation task: {task}\nResult: {result}",
                    metadata={
                        "type": "delegation",
                        "parent_agent": parent_agent,
                        "child_agent": agent_type,
                        "task": task[:200],
                        "depth": depth,
                        "timestamp": timestamp,
                    },
                    doc_id_prefix=f"delegation_{ts_int}",
                )
            except Exception as e:
                logger.warning("Failed to embed delegation result: %s", e)

        return result

    def _extract_task_id(self, task: str) -> str:
        """
        Extract a task ID from a task description string.

        Looks for patterns like 'T170', 'T001', etc.
        Returns empty string if no task ID found.
        """
        match = re.search(r'\bT\d{2,4}\b', task)
        return match.group(0) if match else ""

    def _build_sub_context(self, task: str, context: str, depth: int) -> str:
        """Slim down context for sub-agent: task, top-3 recall, depth indicator."""
        lines = [
            f"## Delegation depth: {depth + 1}",
            f"## Your focused task: {task}",
            "",
        ]
        if context:
            lines.append("## Parent Context (trimmed)")
            lines.append(context[:1000])
            lines.append("")

        # Seed with semantically relevant past sessions (top 3 only)
        if self.session and self.session._vector and self.session._vector.available:
            try:
                recalled = self.session.recall(task, n=3)
                if recalled:
                    lines.append(recalled)
            except Exception:
                pass

        return "\n".join(lines)
