"""Integration tests for hook wiring (T104).

- Agent session with PRE_TOOL_USE shell hook blocking bash
- TASK_COMPLETE hook appending to a log file
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from orchid.hooks.events import PRE_TOOL_USE, TASK_COMPLETE, HookEvent
from orchid.hooks.registry import HookRegistry


def _clear_registry():
    HookRegistry().clear()


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class TestPreToolUseBlocksBash:
    """PRE_TOOL_USE hook blocking bash — agent receives synthetic observation."""

    def test_blocked_bash_injects_observation(self):
        from orchid.agents.base import BaseAgent

        registry = HookRegistry()
        registry.register(
            PRE_TOOL_USE,
            lambda e: {"blocked": True, "error": "bash not allowed"} if e.data.get("tool") == "bash" else None,
            mode="sync",
        )

        agent = BaseAgent()
        # Mock the LLM to: first call → try bash, second call → final answer
        call_count = 0

        def fake_call(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    "Thought: I will run bash.\n"
                    "Action: bash\n"
                    'Action Input: {"command": "rm -rf /"}'
                )
            return "Final Answer: done"

        with patch("orchid.agents.base.call", side_effect=fake_call):
            result = agent.run("test task")

        # Agent ran to completion
        assert result is not None
        # The bash command was blocked, not executed
        history_text = " ".join(m.content for m in agent.history)
        assert "BLOCKED by hook" in history_text

    def test_non_bash_tool_not_blocked(self):
        from orchid.agents.base import BaseAgent

        registry = HookRegistry()
        # Only block bash
        registry.register(
            PRE_TOOL_USE,
            lambda e: {"blocked": True} if e.data.get("tool") == "bash" else None,
            mode="sync",
        )

        agent = BaseAgent()
        call_count = 0

        def fake_call(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    "Thought: I will read a file.\n"
                    "Action: read_file\n"
                    'Action Input: {"path": "nonexistent.txt"}'
                )
            return "Final Answer: file read"

        with patch("orchid.agents.base.call", side_effect=fake_call), \
             patch("orchid.agents.base.read_file", return_value="file content"):
            result = agent.run("read a file")

        history_text = " ".join(m.content for m in agent.history)
        # read_file was NOT blocked
        assert "BLOCKED by hook" not in history_text

    def test_shell_hook_blocking_bash_via_loader(self):
        """Shell hook configured to block bash via {"block": true} stdout."""
        from orchid.agents.base import BaseAgent
        from orchid.hooks.loader import HookLoader

        tmp = Path(tempfile.mkdtemp())
        try:
            # Write a shell hook that outputs {"block": true} when tool=bash
            hook_script = tmp / "block_bash.sh"
            hook_script.write_text(
                '#!/bin/sh\n'
                'INPUT=$(cat)\n'
                'TOOL=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[\'data\'][\'tool\'])" 2>/dev/null)\n'
                'if [ "$TOOL" = "bash" ]; then\n'
                '  echo \'{"block": true, "reason": "bash is forbidden"}\'\n'
                'fi\n'
            )
            hook_script.chmod(0o755)

            with patch("orchid.config.configure_for_project"), \
                 patch("orchid.config.get") as mock_get:
                mock_get.return_value = {
                    "enabled": True,
                    "agent": [{
                        "name": "block_bash",
                        "event": "pre_tool_use",
                        "type": "shell",
                        "command": f"sh {hook_script}",
                        "allowlist_check": False,
                        "mode": "sync",
                    }],
                }
                loader = HookLoader(tmp)
                loader.load()

            agent = BaseAgent()
            call_count = 0

            def fake_call(**kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return (
                        "Thought: run bash\n"
                        "Action: bash\n"
                        'Action Input: {"command": "echo hi"}'
                    )
                return "Final Answer: blocked"

            with patch("orchid.agents.base.call", side_effect=fake_call):
                agent.run("test")

            history_text = " ".join(m.content for m in agent.history)
            assert "BLOCKED by hook" in history_text or "bash is forbidden" in history_text
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestTaskCompleteHookAppendsLog:
    """TASK_COMPLETE hook writes to a log file."""

    def test_task_complete_hook_appends_log(self, tmp_path):
        log_file = tmp_path / "hook.log"

        def log_hook(event):
            with open(log_file, "a") as f:
                f.write(json.dumps(event.data) + "\n")

        registry = HookRegistry()
        registry.register(TASK_COMPLETE, log_hook, mode="sync")

        # Fire the event as the orchestrator would
        event = HookEvent.task_complete_event(
            task_id="T001",
            result="success",
            files=["src/foo.py"],
        )
        registry.fire(event)

        assert log_file.exists()
        logged = json.loads(log_file.read_text().strip())
        assert logged["task_id"] == "T001"
        assert logged["result"] == "success"

    def test_task_complete_hook_fires_after_orchestrator_task(self, tmp_path):
        """Verify orchestrator fires TASK_COMPLETE and hook receives it."""
        log_file = tmp_path / "orch_hook.log"

        def log_hook(event):
            with open(log_file, "a") as f:
                f.write(json.dumps({"task_id": event.data.get("task_id")}) + "\n")

        registry = HookRegistry()
        registry.register(TASK_COMPLETE, log_hook, mode="sync")

        # Simulate what orchestrator._fire_task_complete_hook does
        event = HookEvent(
            event_type=TASK_COMPLETE,
            data={"task_id": "T099", "result": "Task done", "files_written": []},
            context={"task_id": "T099"},
        )
        result = registry.fire(event)

        assert not result.blocked
        assert log_file.exists()
        logged = json.loads(log_file.read_text().strip())
        assert logged["task_id"] == "T099"

    def test_hook_error_does_not_crash_orchestrator_flow(self, tmp_path):
        """A hook that raises must not propagate to caller."""
        def bad_hook(event):
            raise RuntimeError("hook exploded")

        def good_hook(event):
            return "ok"

        registry = HookRegistry()
        registry.register(TASK_COMPLETE, bad_hook, priority=10)
        registry.register(TASK_COMPLETE, good_hook, priority=5)

        event = HookEvent.task_complete_event("T001", "done")
        result = registry.fire(event, ignore_errors=True)

        assert not result.blocked
        assert "ok" in result.results
