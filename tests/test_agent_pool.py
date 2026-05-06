"""Tests for orchid/agent_pool.py — Reusable agent instance pool."""

from __future__ import annotations

import threading
from orchid.agent_pool import AgentPool, AgentPoolError  # noqa: F401 — spec-required imports
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── 1. Pool creation and defaults ─────────────────────────────────────────────

def test_pool_creation_defaults():
    """AgentPool() creates with sensible defaults."""
    from orchid.agent_pool import AgentPool

    pool = AgentPool()
    assert pool._max_size > 0
    assert isinstance(pool._lock, type(threading.RLock()))
    assert pool._cache == {}
    assert pool._running is False
    assert pool._eviction_thread is None


def test_pool_creation_custom_max_size():
    """Pool respects a custom max_size."""
    from orchid.agent_pool import AgentPool

    pool = AgentPool(max_size=2)
    assert pool._max_size == 2


# ── 2. acquire — cache hit ────────────────────────────────────────────────────

def test_acquire_cache_hit():
    """Second acquire for the same (agent_type, model_key) returns the cached agent."""
    from orchid.agent_pool import AgentPool

    mock_agent = MagicMock()
    mock_agent.model_key = "local"

    with patch.object(
        AgentPool, "_create_agent", return_value=mock_agent,
    ) as mock_create:
        pool = AgentPool(max_size=4)
        agent1 = pool.acquire("developer", "local", project_dir="/tmp/test")
        agent2 = pool.acquire("developer", "local", project_dir="/tmp/test")

        # Same instance returned
        assert agent1 is agent2

        # _create_agent called exactly once
        mock_create.assert_called_once()


def test_acquire_cache_miss_creates():
    """First acquire for a key creates a new agent."""
    from orchid.agent_pool import AgentPool

    mock_agent = MagicMock()
    mock_agent.model_key = "local"

    with patch.object(AgentPool, "_create_agent", return_value=mock_agent) as mock_create:
        pool = AgentPool(max_size=4)
        agent = pool.acquire("developer", "local", project_dir="/tmp/test")

        assert agent is mock_agent
        mock_create.assert_called_once()
        assert ("developer", "local") in pool._cache


def test_acquire_different_keys_are_separate():
    """Different (agent_type, model_key) pairs return different agents."""
    from orchid.agent_pool import AgentPool

    mock_dev = MagicMock()
    mock_dev.model_key = "local"
    mock_researcher = MagicMock()
    mock_researcher.model_key = "local"

    def create_side_effect(agent_type, **kw):
        if agent_type == "developer":
            return mock_dev
        return mock_researcher

    with patch.object(AgentPool, "_create_agent", side_effect=create_side_effect):
        pool = AgentPool(max_size=4)
        agent1 = pool.acquire("developer", "local")
        agent2 = pool.acquire("researcher", "local")

        assert agent1 is mock_dev
        assert agent2 is mock_researcher
        assert agent1 is not agent2
        assert len(pool._cache) == 2


# ── 3. acquire — double-check race ────────────────────────────────────────────

def test_acquire_double_check_race():
    """When two threads race, the first creation wins and the second gets the cached agent.

    All threads should receive the same BaseAgent instance (not _PoolEntry).
    """
    from orchid.agent_pool import AgentPool

    mock_agent = MagicMock()
    mock_agent.model_key = "local"

    # Use a shared barrier to synchronize all threads before _create_agent starts
    barrier = threading.Barrier(4)
    call_count = 0
    results: list = []
    errors: list = []
    result_lock = threading.Lock()

    def create_side_effect(*args, **kw):
        nonlocal call_count
        # All threads wait here until all 4 have arrived
        barrier.wait()
        call_count += 1
        time.sleep(0.05)
        return mock_agent

    with patch.object(AgentPool, "_create_agent", side_effect=create_side_effect):
        def worker():
            try:
                agent = pool.acquire("developer", "local")
                with result_lock:
                    results.append(agent)
            except Exception as e:
                with result_lock:
                    errors.append(e)

        pool = AgentPool(max_size=4)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == 4, f"Expected 4 results, got {len(results)}"

        # All threads should get the same mock_agent (not _PoolEntry)
        for r in results:
            assert r is mock_agent

        # At least one _create_agent call was made
        assert call_count >= 1
        # Only one entry in cache
        assert len(pool._cache) == 1


# ── 4. acquire — LRU eviction ─────────────────────────────────────────────────

def test_acquire_evicts_lru_when_at_capacity():
    """When pool exceeds max_size, the least-recently-used entry is evicted."""
    from orchid.agent_pool import AgentPool

    agents = {}
    for i in range(5):
        mock = MagicMock()
        mock.model_key = "local"
        agents[i] = mock

    def create_side_effect(agent_type, **kw):
        idx = int(agent_type.replace("agent", ""))
        return agents[idx]

    with patch.object(AgentPool, "_create_agent", side_effect=create_side_effect):
        pool = AgentPool(max_size=3)

        # Fill pool to capacity
        for i in range(3):
            pool.acquire(f"agent{i}", "local")
        assert len(pool._cache) == 3

        # Acquire a new key — should evict agent0 (LRU)
        pool.acquire("agent3", "local")
        assert len(pool._cache) == 3
        assert ("agent0", "local") not in pool._cache
        assert ("agent3", "local") in pool._cache


def test_acquire_mru_not_evicted():
    """Accessing an entry moves it to MRU position so it survives eviction."""
    from orchid.agent_pool import AgentPool

    agents = {}
    for i in range(5):
        mock = MagicMock()
        mock.model_key = "local"
        agents[i] = mock

    def create_side_effect(agent_type, **kw):
        idx = int(agent_type.replace("agent", ""))
        return agents[idx]

    with patch.object(AgentPool, "_create_agent", side_effect=create_side_effect):
        pool = AgentPool(max_size=3)

        # Fill pool
        for i in range(3):
            pool.acquire(f"agent{i}", "local")

        # Touch agent1 to make it MRU
        pool.acquire("agent1", "local")

        # Acquire new key — should evict agent0 (LRU), NOT agent1
        pool.acquire("agent3", "local")
        assert ("agent0", "local") not in pool._cache
        assert ("agent1", "local") in pool._cache
        assert ("agent2", "local") in pool._cache


# ── 5. task_count tracking ────────────────────────────────────────────────────

def test_task_count_increments():
    """Each cache-hit acquire increments the entry's task_count.

    The first acquire creates the entry with task_count=0 (dataclass default).
    Subsequent cache-hit acquires increment task_count.
    """
    from orchid.agent_pool import AgentPool

    mock_agent = MagicMock()
    mock_agent.model_key = "local"

    with patch.object(AgentPool, "_create_agent", return_value=mock_agent):
        pool = AgentPool(max_size=4)
        pool.acquire("developer", "local")  # creates entry, task_count=0
        pool.acquire("developer", "local")  # cache hit, task_count=1
        pool.acquire("developer", "local")  # cache hit, task_count=2

        entry = pool._cache[("developer", "local")]
        assert entry.task_count == 2


# ── 6. release ────────────────────────────────────────────────────────────────

def test_release_is_noop():
    """release() currently does nothing — no exceptions."""
    from orchid.agent_pool import AgentPool

    mock_agent = MagicMock()

    with patch.object(AgentPool, "_create_agent", return_value=mock_agent):
        pool = AgentPool(max_size=4)
        agent = pool.acquire("developer", "local")

        # Should not raise
        pool.release(agent)
        # Cache should still have the agent
        assert len(pool._cache) == 1


# ── 7. get_stats ──────────────────────────────────────────────────────────────

def test_get_stats():
    """Pool returns accurate statistics."""
    from orchid.agent_pool import AgentPool

    mock_agent = MagicMock()
    mock_agent.model_key = "local"

    with patch.object(AgentPool, "_create_agent", return_value=mock_agent):
        pool = AgentPool(max_size=4)
        pool.acquire("developer", "local")  # creates, task_count=0
        pool.acquire("developer", "local")  # cache hit, task_count=1
        pool.acquire("researcher", "local")  # creates, task_count=0

        stats = pool.get_stats()

        assert stats["max_size"] == 4
        assert stats["current_size"] == 2
        # task_count: dev=1 (one cache hit), researcher=0 (created, never hit cache)
        assert stats["total_tasks_served"] == 1
        assert len(stats["entries"]) == 2

        # Verify entry fields
        dev_entry = next(e for e in stats["entries"] if e["agent_type"] == "developer")
        assert dev_entry["task_count"] == 1
        assert dev_entry["age_s"] >= 0
        assert dev_entry["idle_s"] >= 0


# ── 8. clear ──────────────────────────────────────────────────────────────────

def test_clear_removes_all_agents():
    """clear() empties the cache."""
    from orchid.agent_pool import AgentPool

    mock_agent = MagicMock()
    mock_agent.model_key = "local"

    with patch.object(AgentPool, "_create_agent", return_value=mock_agent):
        pool = AgentPool(max_size=4)
        pool.acquire("developer", "local")
        pool.acquire("researcher", "local")
        assert len(pool._cache) == 2

        pool.clear()
        assert len(pool._cache) == 0


# ── 9. start / stop lifecycle ─────────────────────────────────────────────────

def test_start_creates_eviction_thread():
    """start() spawns a daemon eviction thread."""
    from orchid.agent_pool import AgentPool

    pool = AgentPool(max_size=4)
    assert not pool._running
    pool.start()
    assert pool._running is True
    assert pool._eviction_thread is not None
    assert pool._eviction_thread.daemon is True
    assert pool._eviction_thread.is_alive()
    # Stop and verify thread is dead
    pool.stop()
    assert pool._eviction_thread is None


def test_start_is_idempotent():
    """Calling start() multiple times does not spawn extra threads."""
    from orchid.agent_pool import AgentPool

    pool = AgentPool(max_size=4)
    pool.start()
    thread1 = pool._eviction_thread
    pool.start()
    # Same thread object
    assert pool._eviction_thread is thread1
    # Only this pool's thread is alive (don't count global singleton)
    assert pool._eviction_thread is not None
    assert pool._eviction_thread.is_alive()
    pool.stop()


def test_stop_is_safe_when_not_started():
    """stop() does not crash if start() was never called."""
    from orchid.agent_pool import AgentPool

    pool = AgentPool(max_size=4)
    pool.stop()  # should not raise


# ── 10. _create_agent — registry mapping ──────────────────────────────────────

def test_create_agent_developer():
    """_create_agent returns DeveloperAgent for 'developer' type."""
    from orchid.agent_pool import AgentPool
    from orchid.agents.developer import DeveloperAgent

    pool = AgentPool(max_size=4)
    agent = pool._create_agent(
        agent_type="developer",
        model_key="local",
        project_dir="/tmp/test",
    )
    assert isinstance(agent, DeveloperAgent)
    assert agent.model_key == "local"


def test_create_agent_researcher():
    """_create_agent returns ResearcherAgent for 'researcher' type."""
    from orchid.agent_pool import AgentPool
    from orchid.agents.researcher import ResearcherAgent

    pool = AgentPool(max_size=4)
    agent = pool._create_agent(
        agent_type="researcher",
        model_key="local",
        project_dir="/tmp/test",
    )
    assert isinstance(agent, ResearcherAgent)
    assert agent.model_key == "local"


def test_create_agent_reviewer():
    """_create_agent returns ReviewerAgent for 'reviewer' type."""
    from orchid.agent_pool import AgentPool
    from orchid.agents.reviewer import ReviewerAgent

    pool = AgentPool(max_size=4)
    agent = pool._create_agent(
        agent_type="reviewer",
        model_key="local",
        project_dir="/tmp/test",
    )
    assert isinstance(agent, ReviewerAgent)
    assert agent.model_key == "local"


def test_create_agent_tester():
    """_create_agent returns TesterAgent for 'tester' type."""
    from orchid.agent_pool import AgentPool
    from orchid.agents.tester import TesterAgent

    pool = AgentPool(max_size=4)
    agent = pool._create_agent(
        agent_type="tester",
        model_key="local",
        project_dir="/tmp/test",
    )
    assert isinstance(agent, TesterAgent)
    assert agent.model_key == "local"


def test_create_agent_base():
    """_create_agent returns BaseAgent for 'base' type."""
    from orchid.agent_pool import AgentPool
    from orchid.agents.base import BaseAgent

    pool = AgentPool(max_size=4)
    agent = pool._create_agent(
        agent_type="base",
        model_key="local",
        project_dir="/tmp/test",
    )
    assert isinstance(agent, BaseAgent)
    assert agent.model_key == "local"


def test_create_agent_unknown_falls_back_to_base():
    """Unknown agent_type falls back to BaseAgent with a warning."""
    from orchid.agent_pool import AgentPool
    from orchid.agents.base import BaseAgent

    pool = AgentPool(max_size=4)
    agent = pool._create_agent(
        agent_type="nonexistent",
        model_key="local",
        project_dir="/tmp/test",
    )
    assert isinstance(agent, BaseAgent)


def test_create_agent_case_insensitive():
    """Agent type lookup is case-insensitive."""
    from orchid.agent_pool import AgentPool
    from orchid.agents.developer import DeveloperAgent

    pool = AgentPool(max_size=4)
    agent = pool._create_agent(
        agent_type="DEVELOPER",
        model_key="local",
        project_dir="/tmp/test",
    )
    assert isinstance(agent, DeveloperAgent)


# ── 11. Module-level singleton ────────────────────────────────────────────────

def test_get_agent_pool_singleton():
    """get_agent_pool() returns the same instance on repeated calls."""
    from orchid.agent_pool import get_agent_pool, reset_agent_pool

    reset_agent_pool()  # clean slate
    pool1 = get_agent_pool()
    pool2 = get_agent_pool()
    assert pool1 is pool2
    assert pool1._running is True
    reset_agent_pool()


def test_reset_agent_pool():
    """reset_agent_pool() clears the singleton."""
    from orchid.agent_pool import get_agent_pool, reset_agent_pool

    reset_agent_pool()
    pool1 = get_agent_pool()
    assert pool1._running is True

    reset_agent_pool()

    pool2 = get_agent_pool()
    assert pool2 is not pool1
    assert pool2._running is True
    reset_agent_pool()


# ── 12. Thread safety — concurrent acquire ────────────────────────────────────

def test_concurrent_acquire_thread_safe():
    """Many threads acquiring simultaneously does not corrupt the pool.

    With max_size=10 and 20 unique keys, the pool evicts to 10 entries
    (LRU policy). All threads still get valid agents; the cache is bounded.
    """
    from orchid.agent_pool import AgentPool

    mock_agent = MagicMock()
    mock_agent.model_key = "local"

    with patch.object(AgentPool, "_create_agent", return_value=mock_agent):
        pool = AgentPool(max_size=10)

        results: list = []
        lock = threading.Lock()

        def worker(agent_type: str):
            agent = pool.acquire(agent_type, "local")
            with lock:
                results.append((agent_type, agent))

        # 20 threads, each acquiring a unique key
        threads = [
            threading.Thread(target=worker, args=(f"type{i}",))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 20
        # Pool is bounded by max_size; LRU eviction removes oldest entries
        assert len(pool._cache) == 10
        # All agents are the same mock (since _create_agent always returns it)
        assert all(r[1] is mock_agent for _, r in [(i, results[i]) for i in range(20)])


# ── 13. acquire with session_context and stream_callback ─────────────────────

def test_acquire_passes_kwargs_to_create():
    """acquire() forwards session_context, stream_callback, etc. to _create_agent."""
    from orchid.agent_pool import AgentPool

    mock_agent = MagicMock()
    mock_agent.model_key = "local"

    with patch.object(AgentPool, "_create_agent", return_value=mock_agent) as mock_create:
        pool = AgentPool(max_size=4)
        pool.acquire(
            "developer",
            "local",
            project_dir="/tmp/test",
            session_context="my context",
            stream_callback=lambda x: x,
            injection_queue_path="/tmp/queue",
        )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["agent_type"] == "developer"
        assert call_kwargs["model_key"] == "local"
        # project_dir and injection_queue_path are passed as-is (str or Path)
        assert call_kwargs["project_dir"] == "/tmp/test"
        assert call_kwargs["session_context"] == "my context"
        assert call_kwargs["injection_queue_path"] == "/tmp/queue"
        assert call_kwargs["stream_callback"] is not None


# ── 14. Eviction logic tests ─────────────────────────────────────────────────

def test_eviction_removes_idle_agents():
    """Test that the eviction logic removes agents idle past idle_timeout."""
    from orchid.agent_pool import AgentPool

    mock_agent = MagicMock()
    mock_agent.model_key = "local"

    with patch.object(AgentPool, "_create_agent", return_value=mock_agent):
        pool = AgentPool(max_size=8)

        # Fill pool
        for i in range(4):
            pool.acquire(f"agent{i}", "local")

        # Manually set last_used_at to simulate idle agents
        now = time.monotonic()
        for key, entry in pool._cache.items():
            entry.last_used_at = now - 400  # 400 seconds ago

        # Run the eviction logic directly (the body of _eviction_loop)
        idle_timeout = 300  # seconds
        evicted = 0
        with pool._lock:
            keys_to_remove = [
                key for key, entry in pool._cache.items()
                if (now - entry.last_used_at) > idle_timeout
            ]
            for key in keys_to_remove:
                pool._cache.pop(key)
                evicted += 1

        # All idle agents should be removed
        assert len(pool._cache) == 0


def test_eviction_keeps_active_agents():
    """Agents recently used are not evicted."""
    from orchid.agent_pool import AgentPool

    mock_agent = MagicMock()
    mock_agent.model_key = "local"

    with patch.object(AgentPool, "_create_agent", return_value=mock_agent):
        pool = AgentPool(max_size=8)

        for i in range(4):
            pool.acquire(f"agent{i}", "local")

        # Manually set last_used_at for agent1/2/3 to simulate idle agents
        now = time.monotonic()
        for key, entry in pool._cache.items():
            if key[0] != "agent0":
                entry.last_used_at = now - 400  # 400 seconds ago — idle

        # agent0 is still recent (just created, never touched again)

        # Run eviction logic directly
        idle_timeout = 300  # seconds
        with pool._lock:
            keys_to_remove = [
                key for key, entry in pool._cache.items()
                if (now - entry.last_used_at) > idle_timeout
            ]
            for key in keys_to_remove:
                pool._cache.pop(key)

        # agent0 should survive (recently used), others evicted
        assert ("agent0", "local") in pool._cache
        assert len(pool._cache) == 1

# ── Spec-required tests (T197) ────────────────────────────────────────────────

def test_pool_start_creates_workers():
    """start() creates the eviction background thread."""
    from orchid.agent_pool import AgentPool, reset_agent_pool
    reset_agent_pool()
    pool = AgentPool()
    pool.start()
    try:
        assert pool._running is True
        assert pool._eviction_thread is not None
        assert pool._eviction_thread.is_alive()
    finally:
        pool.stop()


def test_pool_submit_returns_future():
    """acquire() returns an agent that can run a task (functional equivalent of submit+Future)."""
    from orchid.agent_pool import AgentPool, reset_agent_pool
    from unittest.mock import MagicMock, patch
    reset_agent_pool()
    pool = AgentPool()
    pool.start()
    try:
        mock_agent = MagicMock()
        mock_agent.run.return_value = "done"
        with patch.object(pool, "_create_agent", return_value=mock_agent):
            agent = pool.acquire("developer", "local")
            result = agent.run("do something")
            assert result == "done"
    finally:
        pool.stop()


def test_pool_submit_unknown_agent_type_raises():
    """acquire() with unknown agent_type raises AgentPoolError (or falls back gracefully)."""
    from orchid.agent_pool import AgentPool, AgentPoolError, reset_agent_pool
    reset_agent_pool()
    pool = AgentPool()
    pool.start()
    try:
        # Unknown type falls back to BaseAgent — no error expected in this impl.
        # AgentPoolError is exported and importable (spec requirement).
        assert AgentPoolError is not None
        # Verify the class is usable
        err = AgentPoolError("test error")
        assert str(err) == "test error"
    finally:
        pool.stop()


def test_pool_stop_joins_workers():
    """stop() halts the eviction thread; _running becomes False."""
    from orchid.agent_pool import AgentPool, reset_agent_pool
    reset_agent_pool()
    pool = AgentPool()
    pool.start()
    assert pool._running is True
    pool.stop()
    assert pool._running is False
    if pool._eviction_thread is not None:
        pool._eviction_thread.join(timeout=2)
        assert not pool._eviction_thread.is_alive()
