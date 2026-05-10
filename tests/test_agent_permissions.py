"""Tests for per-agent-type tool capability restrictions (T155/T156)."""
from __future__ import annotations

from unittest.mock import patch

from orchid.agents.developer import DeveloperAgent
from orchid.agents.reviewer import ReviewerAgent
from orchid.agents.tester import TesterAgent


def _make_agent(cls, cfg_override=None):
    """Instantiate an agent with project_dir=None and optional cfg mock."""
    if cfg_override is not None:
        with patch("orchid.agents.base.cfg") as mock_cfg:
            mock_cfg.get.side_effect = lambda key, default=None: (
                cfg_override if key == "agents.allowed_tools" else default
            )
            return cls()
    return cls()


def test_developer_has_write_and_bash():
    agent = _make_agent(DeveloperAgent)
    assert "write_file" in agent.tools
    assert "bash" in agent.tools
    assert "read_file" in agent.tools


def test_tester_cannot_write():
    agent = _make_agent(TesterAgent)
    assert "write_file" not in agent.tools
    assert "append_file" not in agent.tools
    assert "read_file" in agent.tools
    assert "bash" in agent.tools


def test_reviewer_cannot_write():
    agent = _make_agent(ReviewerAgent)
    assert "write_file" not in agent.tools
    assert "append_file" not in agent.tools
    assert "read_file" in agent.tools
    assert "check_imports" in agent.tools


def test_yaml_override_restricts_tools():
    # Simulate .orchid.yaml: agents.allowed_tools.developer: [read_file]
    agent = _make_agent(DeveloperAgent, cfg_override={"developer": ["read_file"]})
    assert set(agent.tools.keys()) == {"read_file"}
