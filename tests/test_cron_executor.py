"""Unit tests for orchid.cron.executor.TaskExecutor."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


from orchid.cron.executor import TaskExecutor, TaskExecutionError
from orchid.cron.types import TaskRun


@pytest.fixture
def executor():
    return TaskExecutor()


class TestTaskExecutorAgentPrompt:
    def test_missing_prompt_returns_failure(self, executor):
        run = executor.execute(
            {"task_id": "t1", "task_type": "agent_prompt", "config": {}, "name": "T"},
            "u1",
        )
        assert isinstance(run, TaskRun)
        assert run.status == "failure"
        assert "prompt" in run.error.lower()

    def test_success_with_mocked_provider(self, executor):
        mock_provider = MagicMock()
        mock_provider.complete.return_value = "response text"

        mock_registry = MagicMock()
        mock_registry.resolve.return_value = mock_provider

        with patch("orchid.providers.registry.get_registry", return_value=mock_registry):
            run = executor.execute(
                {
                    "task_id": "t1",
                    "task_type": "agent_prompt",
                    "config": {"prompt": "hello"},
                    "name": "T",
                },
                "u1",
            )

        assert isinstance(run, TaskRun)
        assert run.status == "success"
        assert run.output == "response text"

    def test_provider_exception_returns_failure(self, executor):
        mock_provider = MagicMock()
        mock_provider.complete.side_effect = RuntimeError("boom")

        mock_registry = MagicMock()
        mock_registry.resolve.return_value = mock_provider

        with patch("orchid.providers.registry.get_registry", return_value=mock_registry):
            run = executor.execute(
                {
                    "task_id": "t1",
                    "task_type": "agent_prompt",
                    "config": {"prompt": "hello"},
                    "name": "T",
                },
                "u1",
            )

        assert isinstance(run, TaskRun)
        assert run.status == "failure"
        assert "RuntimeError" in run.error


class TestTaskExecutorMCPTool:
    def test_missing_server_returns_failure(self, executor):
        run = executor.execute(
            {
                "task_id": "t1",
                "task_type": "mcp_tool",
                "config": {"tool": "search", "args": {}},
                "name": "T",
            },
            "u1",
        )
        assert isinstance(run, TaskRun)
        assert run.status == "failure"
        assert "server" in run.error.lower()

    def test_missing_tool_returns_failure(self, executor):
        run = executor.execute(
            {
                "task_id": "t1",
                "task_type": "mcp_tool",
                "config": {"server": "gmail", "args": {}},
                "name": "T",
            },
            "u1",
        )
        assert isinstance(run, TaskRun)
        assert run.status == "failure"
        assert "tool" in run.error.lower()

    def test_success_with_mocked_mcp(self, executor):
        mock_result = MagicMock()
        mock_result.content = "found 5 emails"

        mock_adapter = MagicMock()
        mock_adapter.call_tool.return_value = mock_result

        mock_manager = MagicMock()
        mock_manager.get_adapter.return_value = mock_adapter

        with patch("orchid.mcp.manager.MCPManager", return_value=mock_manager):
            run = executor.execute(
                {
                    "task_id": "t1",
                    "task_type": "mcp_tool",
                    "config": {
                        "server": "gmail",
                        "tool": "search_threads",
                        "args": {"query": "test"},
                    },
                    "name": "T",
                },
                "u1",
            )

        assert isinstance(run, TaskRun)
        assert run.status == "success"

    def test_server_not_found_returns_failure(self, executor):
        mock_manager = MagicMock()
        mock_manager.get_adapter.return_value = None

        with patch("orchid.mcp.manager.MCPManager", return_value=mock_manager):
            run = executor.execute(
                {
                    "task_id": "t1",
                    "task_type": "mcp_tool",
                    "config": {
                        "server": "gmail",
                        "tool": "search_threads",
                        "args": {"query": "test"},
                    },
                    "name": "T",
                },
                "u1",
            )

        assert isinstance(run, TaskRun)
        assert run.status == "failure"
        assert "not found" in run.error.lower()


class TestTaskExecutorShell:
    def test_missing_command_returns_failure(self, executor):
        run = executor.execute(
            {
                "task_id": "t1",
                "task_type": "shell",
                "config": {},
                "name": "T",
            },
            "u1",
        )
        assert isinstance(run, TaskRun)
        assert run.status == "failure"
        assert "command" in run.error.lower()

    def test_success_echo(self, executor):
        run = executor.execute(
            {
                "task_id": "t1",
                "task_type": "shell",
                "config": {"command": "echo hello", "timeout_sec": 10},
                "name": "T",
            },
            "u1",
        )
        assert isinstance(run, TaskRun)
        if run.status == "success":
            assert "hello" in run.output
        else:
            # echo may be blocked by allowlist; test must not fail either way
            assert "allowlist" in run.error.lower()


class TestTaskExecutorUnknownType:
    def test_unknown_type_returns_failure(self, executor):
        run = executor.execute(
            {
                "task_id": "t1",
                "task_type": "invalid_type",
                "config": {},
                "name": "T",
            },
            "u1",
        )
        assert isinstance(run, TaskRun)
        assert run.status == "failure"
        assert "Unknown task_type" in run.error


class TestTaskExecutorNeverRaises:
    def test_execute_never_raises(self, executor, monkeypatch):
        """Even a wildly broken dispatch function must not propagate exceptions."""

        def broken_dispatch(config):
            raise SystemExit(1)

        monkeypatch.setattr(TaskExecutor, "_DISPATCH", {"shell": broken_dispatch})

        run = executor.execute(
            {
                "task_id": "t1",
                "task_type": "shell",
                "config": {},
                "name": "T",
            },
            "u1",
        )

        assert isinstance(run, TaskRun)
        assert run.status == "failure"
        assert "SystemExit" in run.error
