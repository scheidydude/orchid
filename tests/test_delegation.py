"""Tests for agent-to-agent delegation (Milestone 2.3)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_embed(text: str) -> list[float]:
    """Deterministic fake embedding."""
    import math
    seed = sum(ord(c) for c in text[:64])
    vals = [math.sin(seed * (i + 1)) for i in range(8)]
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]


# ── 1. Delegate action parsed correctly ──────────────────────────────────────

def test_delegate_action_parsed():
    """Base agent parses 'Action: delegate[researcher | find X]' without error."""
    from orchid.agents.base import _ACTION_BRACKET_RE

    line = "Action: delegate[researcher | find the best Python PDF library]"
    m = _ACTION_BRACKET_RE.search(line)
    assert m is not None
    assert m.group(1) == "delegate"
    arg_value = m.group(2)
    assert "researcher" in arg_value
    assert "find the best Python PDF library" in arg_value

    # _do_delegate splits on "|"
    agent_type, task = arg_value.split("|", 1)
    assert agent_type.strip() == "researcher"
    assert task.strip() == "find the best Python PDF library"


# ── 2. Delegator instantiates correct agent class ─────────────────────────────

def test_delegator_instantiates_correct_agent():
    from orchid.agents.delegator import _get_agent_class
    from orchid.agents.developer import DeveloperAgent
    from orchid.agents.researcher import ResearcherAgent
    from orchid.agents.reviewer import ReviewerAgent
    from orchid.agents.base import BaseAgent

    assert _get_agent_class("developer") is DeveloperAgent
    assert _get_agent_class("researcher") is ResearcherAgent
    assert _get_agent_class("reviewer") is ReviewerAgent
    assert _get_agent_class("base") is BaseAgent
    assert _get_agent_class("DEVELOPER") is DeveloperAgent  # case-insensitive


# ── 3. Unknown agent type raises ─────────────────────────────────────────────

def test_unknown_agent_type_raises():
    from orchid.agents.delegator import _get_agent_class

    with pytest.raises(ValueError, match="Unknown agent type"):
        _get_agent_class("nonexistent_agent")


# ── 4. Depth limit enforced ───────────────────────────────────────────────────

def test_delegation_depth_limit_enforced():
    from orchid.agents.delegator import AgentDelegator

    with patch("orchid.config.get") as mock_cfg:
        def cfg_side(key, default=None):
            if key == "delegation.max_depth":
                return 3
            if key == "delegation.enabled":
                return True
            return default
        mock_cfg.side_effect = cfg_side

        delegator = AgentDelegator()
        result = delegator.delegate(
            agent_type="researcher",
            task="find something",
            context="ctx",
            depth=3,  # at max depth
        )
        assert "max depth" in result
        assert "refused" in result


# ── 5. Sub-context is slimmed ─────────────────────────────────────────────────

def test_sub_context_is_slimmed():
    from orchid.agents.delegator import AgentDelegator

    delegator = AgentDelegator(session=None, vector_memory=None)
    long_ctx = "X" * 5000
    sub_ctx = delegator._build_sub_context("find something", long_ctx, depth=1)

    assert "Delegation depth: 2" in sub_ctx
    assert "Your focused task: find something" in sub_ctx
    # Parent context trimmed to 1000 chars
    assert len(sub_ctx) < 3000


# ── 6. Delegation result embedded into vector store ──────────────────────────

def test_delegation_result_embedded(tmp_path):
    from orchid.agents.delegator import AgentDelegator
    from orchid.memory.vector import VectorMemory

    with patch("orchid.tools.models.embed", side_effect=_fake_embed):
        vm = VectorMemory(project_dir=tmp_path)
        if not vm.available:
            pytest.skip("chromadb not available")

        delegator = AgentDelegator(vector_memory=vm, project_name="test")

        # Build a fake agent class that accepts the researcher kwargs
        class FakeAgent:
            def __init__(self, session_context="", vector_memory=None, project_name=""):
                self.delegator = None
                self.delegation_depth = 0
                self.max_iterations = 5

            def run(self, task):
                return "Found: httpx supports retries natively."

        with patch("orchid.agents.delegator._get_agent_class", return_value=FakeAgent):
            with patch("orchid.config.get") as mock_cfg:
                def cfg_side(key, default=None):
                    return {
                        "delegation.max_depth": 3,
                        "delegation.enabled": True,
                        "delegation.max_sub_iterations": 5,
                        "delegation.log_delegations": False,
                        "delegation.embed_results": True,
                    }.get(key, default)
                mock_cfg.side_effect = cfg_side

                delegator.delegate(
                    agent_type="researcher",
                    task="find httpx retry approach",
                    context="",
                    depth=0,
                )

        # Check vector store has the delegation entry
        assert vm.count() > 0


# ── 7. Delegation logged in session ──────────────────────────────────────────

def test_delegation_logged_in_session(tmp_path):
    from orchid.agents.delegator import AgentDelegator

    mock_session = MagicMock()
    mock_session._log_path = tmp_path / "session_test.jsonl"
    mock_session._log_path.touch()
    mock_session._vector = None
    mock_session.recall = MagicMock(return_value="")

    delegator = AgentDelegator(session=mock_session, project_name="test")

    with patch("orchid.config.get") as mock_cfg:
        def cfg_side(key, default=None):
            defaults = {
                "delegation.max_depth": 3,
                "delegation.enabled": True,
                "delegation.max_sub_iterations": 5,
                "delegation.log_delegations": True,
                "delegation.embed_results": False,
            }
            return defaults.get(key, default)
        mock_cfg.side_effect = cfg_side

        with patch("orchid.agents.delegator._get_agent_class") as mock_cls:
            fake_agent = MagicMock()
            fake_agent.run.return_value = "Use tenacity library."
            mock_cls.return_value = lambda **kw: fake_agent
            mock_cls.return_value = type("FA", (), {
                "__init__": lambda self, **kw: None,
                "run": lambda self, t: "Use tenacity library.",
            })

            delegator.delegate(
                agent_type="researcher",
                task="find retry libraries",
                context="",
                depth=0,
            )

    mock_session.record_delegation.assert_called_once()
    record = mock_session.record_delegation.call_args[0][0]
    assert record["child_agent"] == "researcher"
    assert record["task"] == "find retry libraries"
    assert record["depth"] == 0
    assert "result_summary" in record


# ── 8. End-to-end: developer delegates to researcher ─────────────────────────

def test_delegate_researcher_end_to_end():
    """Developer agent delegate action routes correctly to researcher."""
    from orchid.agents.base import BaseAgent

    call_log: list[str] = []

    def fake_call(messages, model_key="local", system=""):
        # First LLM call: emit a delegate action
        if not call_log:
            call_log.append("delegated")
            return "Thought: I need to research this.\nAction: delegate[researcher | find retry strategies for httpx]"
        # Second LLM call: after observation, provide final answer
        call_log.append("answered")
        return "Final Answer: Use tenacity with exponential backoff."

    researcher_result = "httpx + tenacity is best. Use @retry decorator."
    mock_delegator = MagicMock()
    mock_delegator.delegate.return_value = researcher_result

    # Patch call in the module where it's imported (base.py)
    with patch("orchid.agents.base.call", side_effect=fake_call):
        agent = BaseAgent(session_context="test context")
        agent.delegator = mock_delegator
        agent.delegation_depth = 0

        result = agent.run("Implement a retry wrapper for httpx.")

    assert result == "Use tenacity with exponential backoff."
    mock_delegator.delegate.assert_called_once()
    call_args = mock_delegator.delegate.call_args
    assert call_args.kwargs["agent_type"] == "researcher"
    assert "retry" in call_args.kwargs["task"].lower()
