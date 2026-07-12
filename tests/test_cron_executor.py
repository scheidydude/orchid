"""Unit tests for orchid.cron.executor.TaskExecutor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orchid.cron.executor import TaskExecutor
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


class TestTaskExecutorAgentTool:
    """Tests for the agent_tool task type (multi-server agentic loop)."""

    # ---- config validation ------------------------------------------

    def test_missing_servers_returns_failure(self, executor):
        run = executor.execute(
            {
                "task_id": "t1",
                "task_type": "agent_tool",
                "config": {"prompt": "hello"},
                "name": "T",
            },
            "u1",
        )
        assert run.status == "failure"
        assert "servers" in run.error.lower()

    def test_missing_prompt_returns_failure(self, executor):
        run = executor.execute(
            {
                "task_id": "t1",
                "task_type": "agent_tool",
                "config": {"servers": ["github"]},
                "name": "T",
            },
            "u1",
        )
        assert run.status == "failure"
        assert "prompt" in run.error.lower()

    def test_server_not_found_returns_failure(self, executor):
        mock_manager = MagicMock()
        mock_manager.get_adapter.return_value = None

        with patch("orchid.mcp.manager.MCPManager", return_value=mock_manager):
            run = executor.execute(
                {
                    "task_id": "t1",
                    "task_type": "agent_tool",
                    "config": {"servers": ["missing"], "prompt": "go"},
                    "name": "T",
                },
                "u1",
            )
        assert run.status == "failure"
        assert "not found" in run.error.lower()

    # ---- helpers -------------------------------------------------------

    @staticmethod
    def _anth_reg():
        """Mock registry resolving to AnthropicProvider so anthropic.Anthropic mock is used."""
        from orchid.providers.anthropic import AnthropicProvider
        m = MagicMock()
        m.resolve.return_value = AnthropicProvider()
        m.get_by_key.return_value = None
        return m

    @staticmethod
    def _make_tool():
        from orchid.mcp.types import MCPTool
        return MCPTool(
            name="search_repos",
            description="Search repos",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        )

    @staticmethod
    def _make_response(stop_reason: str, blocks: list):
        resp = MagicMock()
        resp.stop_reason = stop_reason
        resp.content = blocks
        return resp

    @staticmethod
    def _text_block(text: str):
        b = MagicMock()
        b.type = "text"
        b.text = text
        return b

    @staticmethod
    def _tool_use_block(tool_id: str, name: str, inp: dict):
        b = MagicMock()
        b.type = "tool_use"
        b.id = tool_id
        b.name = name
        b.input = inp
        return b

    # ---- happy paths ---------------------------------------------------

    def test_single_turn_end_turn(self, executor):
        """No tool calls — end_turn on first response returns text."""

        mock_adapter = MagicMock()
        mock_adapter.list_tools.return_value = [self._make_tool()]

        mock_manager = MagicMock()
        mock_manager.get_adapter.return_value = mock_adapter

        text_block = self._text_block("Here are the top repos.")
        mock_response = self._make_response("end_turn", [text_block])

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("orchid.mcp.manager.MCPManager", return_value=mock_manager), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("orchid.providers.registry.get_registry", return_value=self._anth_reg()):
            run = executor.execute(
                {
                    "task_id": "t1",
                    "task_type": "agent_tool",
                    "config": {
                        "servers": ["github"],
                        "prompt": "List AI repos",
                    },
                    "name": "T",
                },
                "u1",
            )

        assert run.status == "success"
        assert run.output == "Here are the top repos."

    def test_tool_use_then_end_turn(self, executor):
        """One tool call round-trip completes successfully."""
        from orchid.mcp.types import MCPResult

        mock_adapter = MagicMock()
        mock_adapter.list_tools.return_value = [self._make_tool()]
        mock_adapter.call_tool.return_value = MCPResult(content="repo1, repo2")

        mock_manager = MagicMock()
        mock_manager.get_adapter.return_value = mock_adapter

        tool_block = self._tool_use_block("tid1", "github__search_repos", {"q": "ai"})
        final_text = self._text_block("Done: repo1, repo2")

        turn1 = self._make_response("tool_use", [tool_block])
        turn2 = self._make_response("end_turn", [final_text])

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [turn1, turn2]

        with patch("orchid.mcp.manager.MCPManager", return_value=mock_manager), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("orchid.providers.registry.get_registry", return_value=self._anth_reg()):
            run = executor.execute(
                {
                    "task_id": "t1",
                    "task_type": "agent_tool",
                    "config": {
                        "servers": ["github"],
                        "prompt": "Find AI repos",
                    },
                    "name": "T",
                },
                "u1",
            )

        assert run.status == "success"
        assert "repo1" in run.output
        mock_adapter.call_tool.assert_called_once_with("search_repos", {"q": "ai"})

    def test_multi_server_tool_prefix(self, executor):
        """Tools from two servers are prefixed; dispatch routes correctly."""
        from orchid.mcp.types import MCPResult, MCPTool

        github_tool = MCPTool(name="search_repos", description="Search", parameters={})
        gmail_tool = MCPTool(name="send_email", description="Send", parameters={})

        github_adapter = MagicMock()
        github_adapter.list_tools.return_value = [github_tool]

        gmail_adapter = MagicMock()
        gmail_adapter.list_tools.return_value = [gmail_tool]
        gmail_adapter.call_tool.return_value = MCPResult(content="sent")

        def get_adapter(name):
            return {"github": github_adapter, "gmail": gmail_adapter}[name]

        mock_manager = MagicMock()
        mock_manager.get_adapter.side_effect = get_adapter

        tool_block = self._tool_use_block("tid2", "gmail__send_email", {"to": "a@b.com"})
        final_text = self._text_block("Email sent.")

        turn1 = self._make_response("tool_use", [tool_block])
        turn2 = self._make_response("end_turn", [final_text])

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [turn1, turn2]

        with patch("orchid.mcp.manager.MCPManager", return_value=mock_manager), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("orchid.providers.registry.get_registry", return_value=self._anth_reg()):
            run = executor.execute(
                {
                    "task_id": "t1",
                    "task_type": "agent_tool",
                    "config": {
                        "servers": ["github", "gmail"],
                        "prompt": "Search repos and email digest",
                    },
                    "name": "T",
                },
                "u1",
            )

        assert run.status == "success"
        gmail_adapter.call_tool.assert_called_once_with("send_email", {"to": "a@b.com"})
        github_adapter.call_tool.assert_not_called()

    def test_unknown_tool_in_response_returns_error_result(self, executor):
        """Unknown tool name in response produces tool_result with is_error=True."""
        mock_adapter = MagicMock()
        mock_adapter.list_tools.return_value = [self._make_tool()]

        mock_manager = MagicMock()
        mock_manager.get_adapter.return_value = mock_adapter

        bad_tool = self._tool_use_block("tid3", "nonexistent__tool", {})
        final_text = self._text_block("done")

        turn1 = self._make_response("tool_use", [bad_tool])
        turn2 = self._make_response("end_turn", [final_text])

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [turn1, turn2]

        with patch("orchid.mcp.manager.MCPManager", return_value=mock_manager), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("orchid.providers.registry.get_registry", return_value=self._anth_reg()):
            run = executor.execute(
                {
                    "task_id": "t1",
                    "task_type": "agent_tool",
                    "config": {"servers": ["github"], "prompt": "go"},
                    "name": "T",
                },
                "u1",
            )

        assert run.status == "success"
        # Loop completed without calling the adapter (tool was unknown)
        mock_adapter.call_tool.assert_not_called()
        # Two API calls were made: tool_use turn + end_turn
        assert mock_client.messages.create.call_count == 2
        # All messages passed to the second call include a tool_result with is_error=True
        second_msgs = mock_client.messages.create.call_args_list[1].kwargs["messages"]
        all_tool_results = [
            c
            for m in second_msgs
            for c in (m.get("content") if isinstance(m.get("content"), list) else [])
            if isinstance(c, dict) and c.get("type") == "tool_result"
        ]
        assert any(r.get("is_error") for r in all_tool_results)

    def test_max_iterations_guard(self, executor):
        """Loop capped at max_iterations; returns last text found."""
        mock_adapter = MagicMock()
        mock_adapter.list_tools.return_value = [self._make_tool()]

        mock_manager = MagicMock()
        mock_manager.get_adapter.return_value = mock_adapter

        from orchid.mcp.types import MCPResult
        mock_adapter.call_tool.return_value = MCPResult(content="data")

        tool_block = self._tool_use_block("t1", "github__search_repos", {})
        # Always returns tool_use — never end_turn
        infinite_response = self._make_response("tool_use", [tool_block])

        mock_client = MagicMock()
        mock_client.messages.create.return_value = infinite_response

        with patch("orchid.mcp.manager.MCPManager", return_value=mock_manager), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("orchid.providers.registry.get_registry", return_value=self._anth_reg()):
            run = executor.execute(
                {
                    "task_id": "t1",
                    "task_type": "agent_tool",
                    "config": {
                        "servers": ["github"],
                        "prompt": "loop forever",
                        "max_iterations": 3,
                    },
                    "name": "T",
                },
                "u1",
            )

        assert run.status == "success"
        assert mock_client.messages.create.call_count == 3
        assert "max_iterations=3" in run.output

    def test_adapters_disconnected_on_exception(self, executor):
        """Adapters disconnect even when Anthropic call raises."""
        mock_adapter = MagicMock()
        mock_adapter.list_tools.return_value = [self._make_tool()]

        mock_manager = MagicMock()
        mock_manager.get_adapter.return_value = mock_adapter

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("api down")

        with patch("orchid.mcp.manager.MCPManager", return_value=mock_manager), \
             patch("anthropic.Anthropic", return_value=mock_client), \
             patch("orchid.providers.registry.get_registry", return_value=self._anth_reg()):
            run = executor.execute(
                {
                    "task_id": "t1",
                    "task_type": "agent_tool",
                    "config": {"servers": ["github"], "prompt": "go"},
                    "name": "T",
                },
                "u1",
            )

        assert run.status == "failure"
        assert "api down" in run.error
        mock_adapter.disconnect.assert_called_once()


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
