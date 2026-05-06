"""orchid/agent_pool.py — Reusable agent instance pool.

Responsibilities:
  - Maintain a pool of pre-instantiated agent objects keyed by (agent_type, model_key)
  - Reuse agents across tasks to avoid repeated construction overhead
  - Evict idle agents when pool size exceeds the configured maximum
  - Thread-safe access via RLock

Architecture:
  D0021: Parallelism — the pool must be safe for concurrent access from
         multiple threads (BackgroundRunner parallel dispatch).
  T193: Agent pool — centralised agent lifecycle management.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchid import config as cfg

if TYPE_CHECKING:
    from orchid.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class AgentPoolError(Exception):
    pass


@dataclass
class _PoolEntry:
    """A single cached agent instance with metadata."""
    agent: BaseAgent
    model_key: str
    created_at: float = field(default_factory=time.monotonic)
    last_used_at: float = field(default_factory=time.monotonic)
    task_count: int = 0


class AgentPool:
    """
    Thread-safe pool of reusable agent instances.

    Agents are cached by (agent_type, model_key) tuple.  When a task needs
    an agent, the pool is consulted first; if a matching entry exists it is
    returned and its last_used_at timestamp is updated.  If no entry exists
    a new agent is instantiated and added to the pool.

    When the pool exceeds max_size, the least-recently-used idle entry is
    evicted.  This keeps the pool bounded while maximising hit rate.

    Usage:
        pool = AgentPool()
        pool.start()          # optional: start idle-eviction background thread
        agent = pool.acquire(agent_type="developer", model_key="local", project_dir=...)
        result = agent.run(task_desc)
        pool.release(agent)   # return to pool for reuse
        pool.stop()           # optional: stop eviction thread
    """

    def __init__(self, max_size: int | None = None) -> None:
        self._max_size = max_size or cfg.get("agent_pool.max_size", 8)
        self._lock = threading.RLock()
        # OrderedDict: LRU ordering — access updates position to end
        self._cache: OrderedDict[tuple[str, str], _PoolEntry] = OrderedDict()
        self._running = False
        self._eviction_thread: threading.Thread | None = None

    # ── Public API ──────────────────────────────────────────────────────────────

    def acquire(
        self,
        agent_type: str,
        model_key: str,
        project_dir: str | Path | None = None,
        session_context: str = "",
        stream_callback: Any | None = None,
        injection_queue_path: str | Path | None = None,
    ) -> BaseAgent:
        """
        Get an agent instance for the given (agent_type, model_key) pair.

        Returns a cached agent if one exists, otherwise creates a new one.
        The agent's last_used_at timestamp is updated on every acquire.
        """
        key = (agent_type, model_key)

        with self._lock:
            # Try to find a cached entry
            if key in self._cache:
                entry = self._cache[key]
                entry.last_used_at = time.monotonic()
                entry.task_count += 1
                # Move to end (most recently used)
                self._cache.move_to_end(key)
                logger.debug(
                    "AgentPool HIT: %s (model=%s, tasks=%d)",
                    agent_type, model_key, entry.task_count,
                )
                return entry.agent

        # Cache miss — create a new agent (outside lock to avoid blocking)
        agent = self._create_agent(
            agent_type=agent_type,
            model_key=model_key,
            project_dir=project_dir,
            session_context=session_context,
            stream_callback=stream_callback,
            injection_queue_path=injection_queue_path,
        )

        with self._lock:
            # Double-check: another thread may have created one while we were
            # constructing the new agent.
            if key in self._cache:
                cached = self._cache[key]
                cached.last_used_at = time.monotonic()
                cached.task_count += 1
                self._cache.move_to_end(key)
                logger.debug(
                    "AgentPool double-check HIT: %s (model=%s, tasks=%d)",
                    agent_type, model_key, cached.task_count,
                )
                return cached.agent  # BUG FIX: was returning 'cached' (_PoolEntry)

            # Insert new entry
            entry = _PoolEntry(agent=agent, model_key=model_key)
            self._cache[key] = entry

            # Evict LRU entry if pool is at capacity
            while len(self._cache) > self._max_size:
                evicted_key, evicted_entry = self._cache.popitem(last=False)
                logger.debug(
                    "AgentPool evicted: %s (model=%s, tasks=%d)",
                    evicted_key[0], evicted_key[1], evicted_entry.task_count,
                )

        return agent

    def release(self, agent: BaseAgent) -> None:
        """
        Release an agent back to the pool.

        Currently a no-op — agents are returned implicitly when acquire()
        is called again (the pool returns the same cached instance).
        The release method exists for future use (e.g. health checks,
        forced eviction on errors).
        """
        # Future: validate agent health, reset state, etc.
        pass

    def get_stats(self) -> dict[str, Any]:
        """Return pool statistics for monitoring."""
        with self._lock:
            total_agents = len(self._cache)
            total_tasks = sum(e.task_count for e in self._cache.values())
            entries = [
                {
                    "agent_type": k[0],
                    "model_key": k[1],
                    "task_count": e.task_count,
                    "age_s": round(time.monotonic() - e.created_at, 1),
                    "idle_s": round(time.monotonic() - e.last_used_at, 1),
                }
                for k, e in self._cache.items()
            ]
        return {
            "max_size": self._max_size,
            "current_size": total_agents,
            "total_tasks_served": total_tasks,
            "entries": entries,
        }

    def clear(self) -> None:
        """Evict all cached agents. Call when session ends or config changes."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info("AgentPool cleared: %d agents removed", count)

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background eviction thread. Safe to call multiple times."""
        if self._running:
            return
        self._running = True
        self._eviction_thread = threading.Thread(
            target=self._eviction_loop,
            name="orchid-agent-pool-evict",
            daemon=True,
        )
        self._eviction_thread.start()
        logger.info("AgentPool started (max_size=%d)", self._max_size)

    def stop(self) -> None:
        """Stop the background eviction thread."""
        self._running = False
        if self._eviction_thread is not None:
            self._eviction_thread.join(timeout=2.0)
            self._eviction_thread = None
        logger.info("AgentPool stopped")

    # ── Internal ────────────────────────────────────────────────────────────────

    def _create_agent(
        self,
        agent_type: str,
        model_key: str,
        project_dir: str | Path | None = None,
        session_context: str = "",
        stream_callback: Any | None = None,
        injection_queue_path: str | Path | None = None,
    ) -> BaseAgent:
        """Instantiate a new agent of the given type."""
        from orchid.agents.base import BaseAgent
        from orchid.agents.developer import DeveloperAgent
        from orchid.agents.researcher import ResearcherAgent
        from orchid.agents.reviewer import ReviewerAgent
        from orchid.agents.tester import TesterAgent

        _registry: dict[str, type[BaseAgent]] = {
            "developer": DeveloperAgent,
            "researcher": ResearcherAgent,
            "reviewer": ReviewerAgent,
            "tester": TesterAgent,
            "base": BaseAgent,
        }

        agent_cls = _registry.get(agent_type.lower().strip())
        if agent_cls is None:
            logger.warning("Unknown agent_type '%s' — falling back to BaseAgent", agent_type)
            agent_cls = BaseAgent

        # Researcher needs vector_memory for web search tools
        if agent_type.lower().strip() == "researcher":
            from orchid.session import get_current_session
            session = get_current_session()
            vector_memory = session._vector if session else None
            agent = agent_cls(
                session_context=session_context,
                vector_memory=vector_memory,
                project_name=session.project_name if session else "",
                project_dir=project_dir,
                stream_callback=stream_callback,
                injection_queue_path=injection_queue_path,
            )
        else:
            agent = agent_cls(
                session_context=session_context,
                project_dir=project_dir,
                stream_callback=stream_callback,
                injection_queue_path=injection_queue_path,
            )

        agent.model_key = model_key
        return agent

    def _eviction_loop(self) -> None:
        """Background thread that periodically evicts stale agents."""
        interval = cfg.get("agent_pool.eviction_interval", 60)  # seconds
        idle_timeout = cfg.get("agent_pool.idle_timeout", 300)   # seconds

        while self._running:
            time.sleep(interval)
            now = time.monotonic()
            evicted = 0

            with self._lock:
                # Remove entries idle longer than idle_timeout
                keys_to_remove = [
                    key for key, entry in self._cache.items()
                    if (now - entry.last_used_at) > idle_timeout
                ]
                for key in keys_to_remove:
                    self._cache.pop(key)
                    evicted += 1

            if evicted:
                logger.info("AgentPool eviction: %d idle agents removed", evicted)


# ── Module-level singleton ────────────────────────────────────────────────────

# Global pool instance — created lazily on first use.
# BackgroundRunner and AgentDelegator both reference this.
_pool_instance: AgentPool | None = None
_pool_lock = threading.Lock()


def get_agent_pool() -> AgentPool:
    """Return the global AgentPool singleton, creating it if needed."""
    global _pool_instance
    if _pool_instance is None:
        with _pool_lock:
            if _pool_instance is None:
                _pool_instance = AgentPool()
                _pool_instance.start()
    return _pool_instance


def reset_agent_pool() -> None:
    """Reset the global pool. Call when session ends or config changes."""
    global _pool_instance
    with _pool_lock:
        if _pool_instance is not None:
            _pool_instance.stop()
            _pool_instance.clear()
            _pool_instance = None
        logger.info("Global AgentPool reset")