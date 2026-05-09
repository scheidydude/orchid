"""Tests for shell agent_id parameter (T239)."""

from __future__ import annotations

import pytest
from orchid.errors import ToolError
from orchid.tools.shell import bash


def test_bash_with_no_agent_id_executes_normally():
    """Call bash("echo hello") with no agent_id. Assert result contains "hello"."""
    result = bash("echo hello")
    assert "hello" in result


def test_bash_with_agent_id_executes_normally():
    """Call bash("echo hello") with a non-empty agent_id. Assert result contains "hello"."""
    result = bash("echo hello", agent_id="T001")
    assert "hello" in result


def test_bash_blocked_pattern_still_applies_with_agent_id():
    """Blocklist patterns must still apply even when agent_id is provided."""
    with pytest.raises(ToolError, match="Blocked"):
        bash("rm -rf /", agent_id="T001")