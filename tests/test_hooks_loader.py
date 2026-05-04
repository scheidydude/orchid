"""Tests for HookLoader — shell stdin/stdout blocking, hooks.py decorator (T103)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from orchid.hooks.events import HookEvent, TASK_START, TASK_COMPLETE, PRE_TOOL_USE
from orchid.hooks.loader import HookLoadError, HookLoader
from orchid.hooks.registry import HookRegistry


def _clear_registry():
    HookRegistry().clear()


class TestShellHookStdinStdout:
    def setup_method(self):
        _clear_registry()
        self.tmp = Path(tempfile.mkdtemp())

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        _clear_registry()

    def _make_loader_with_shell_hook(self, command: str, event: str = "task_start") -> HookLoader:
        with patch("orchid.config.configure_for_project"), \
             patch("orchid.config.get") as mock_get:
            mock_get.return_value = {
                "enabled": True,
                "tasks": [{"name": "test", "event": event, "type": "shell", "command": command, "allowlist_check": False}],
            }
            loader = HookLoader(self.tmp)
            loader.load()
        return loader

    def test_shell_hook_receives_context_on_stdin(self):
        log = self.tmp / "stdin.json"
        loader = self._make_loader_with_shell_hook(f"cat > {log}")
        event = HookEvent(event_type="task_start", data={"task_id": "T001"})
        handlers = loader.registry.get_handlers_for_event("task_start")
        handlers[0].handler(event)
        assert log.exists()
        data = json.loads(log.read_text())
        assert data["event_type"] == "task_start"
        assert data["data"]["task_id"] == "T001"

    def test_shell_hook_block_true_sets_blocked(self):
        cmd = "echo '{\"block\": true, \"reason\": \"test block\"}'"
        loader = self._make_loader_with_shell_hook(cmd)
        event = HookEvent(event_type="task_start")
        handlers = loader.registry.get_handlers_for_event("task_start")
        result = handlers[0].handler(event)
        assert isinstance(result, dict)
        assert result["blocked"] is True
        assert "test block" in result["error"]

    def test_shell_hook_block_propagated_via_registry(self):
        cmd = "echo '{\"block\": true, \"reason\": \"no bash\"}'"
        loader = self._make_loader_with_shell_hook(cmd, event="pre_tool_use")
        event = HookEvent(event_type="pre_tool_use", data={"tool": "bash"})
        hook_result = loader.registry.fire(event)
        assert hook_result.blocked is True
        assert "no bash" in (hook_result.error or "")

    def test_shell_hook_mutated_context_returned(self):
        cmd = 'echo \'{"mutated_context": {"extra": "injected"}}\''
        loader = self._make_loader_with_shell_hook(cmd)
        event = HookEvent(event_type="task_start")
        handlers = loader.registry.get_handlers_for_event("task_start")
        result = handlers[0].handler(event)
        assert isinstance(result, dict)
        assert result["mutated_context"]["extra"] == "injected"

    def test_shell_hook_plain_stdout_returned_as_string(self):
        cmd = "echo 'hello world'"
        loader = self._make_loader_with_shell_hook(cmd)
        event = HookEvent(event_type="task_start")
        handlers = loader.registry.get_handlers_for_event("task_start")
        result = handlers[0].handler(event)
        assert isinstance(result, str)
        assert "hello world" in result

    def test_shell_hook_command_not_in_allowlist_blocked(self):
        with patch("orchid.config.configure_for_project"), \
             patch("orchid.config.get") as mock_get:
            mock_get.side_effect = lambda key, default=None: (
                [] if key == "hooks.shell_allowlist" else
                {"enabled": True, "tasks": [{"name": "t", "event": "task_start", "type": "shell", "command": "rm -rf /tmp/x", "allowlist_check": True}]}
            )
            loader = HookLoader(self.tmp)
            loader.load()
        event = HookEvent(event_type="task_start")
        handlers = loader.registry.get_handlers_for_event("task_start")
        result = handlers[0].handler(event)
        assert result == "[blocked]"


class TestHooksFilePy:
    def setup_method(self):
        _clear_registry()
        self.tmp = Path(tempfile.mkdtemp())

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        _clear_registry()

    def _write_hooks_py(self, content: str) -> None:
        (self.tmp / "hooks.py").write_text(content)

    def _make_loader(self) -> HookLoader:
        with patch("orchid.config.configure_for_project"), \
             patch("orchid.config.get") as mock_get:
            mock_get.return_value = {"enabled": True}
            loader = HookLoader(self.tmp)
            loader.load()
        return loader

    def test_orchid_hook_decorator_registers_function(self):
        self._write_hooks_py(
            "from orchid.hooks import orchid_hook, TASK_COMPLETE\n\n"
            "@orchid_hook(TASK_COMPLETE)\n"
            "def on_done(event): return 'done'\n"
        )
        loader = self._make_loader()
        handlers = loader.registry.get_handlers_for_event("task_complete")
        assert len(handlers) == 1

    def test_orchid_hook_fires_and_returns_value(self):
        self._write_hooks_py(
            "from orchid.hooks import orchid_hook, TASK_COMPLETE\n\n"
            "@orchid_hook(TASK_COMPLETE)\n"
            "def on_done(event): return f'task={event.data.get(\"task_id\")}'\n"
        )
        loader = self._make_loader()
        event = HookEvent(event_type="task_complete", data={"task_id": "T042"})
        result = loader.registry.fire(event)
        assert any("T042" in str(r) for r in result.results)

    def test_invalid_hooks_py_raises_hook_load_error(self):
        self._write_hooks_py("this is not valid python @@@@")
        with pytest.raises(HookLoadError):
            loader = HookLoader(self.tmp)
            with patch("orchid.config.configure_for_project"), \
                 patch("orchid.config.get") as mock_get:
                mock_get.return_value = {"enabled": True}
                loader.load()

    def test_hooks_py_without_decorator_not_registered(self):
        self._write_hooks_py(
            "def plain_function(event): return 'no decorator'\n"
        )
        loader = self._make_loader()
        # No handlers should be registered from this hooks.py
        assert loader.registry.registered_count == 0

    def test_multiple_decorated_functions_all_registered(self):
        self._write_hooks_py(
            "from orchid.hooks import orchid_hook, TASK_COMPLETE, TASK_START\n\n"
            "@orchid_hook(TASK_COMPLETE)\n"
            "def on_complete(event): pass\n\n"
            "@orchid_hook(TASK_START)\n"
            "def on_start(event): pass\n"
        )
        loader = self._make_loader()
        assert len(loader.registry.get_handlers_for_event("task_complete")) == 1
        assert len(loader.registry.get_handlers_for_event("task_start")) == 1


class TestHTTPHookPayload:
    def setup_method(self):
        _clear_registry()
        self.tmp = Path(tempfile.mkdtemp())

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        _clear_registry()

    def test_http_hook_posts_correct_payload(self):
        with patch("orchid.config.configure_for_project"), \
             patch("orchid.config.get") as mock_get:
            mock_get.return_value = {
                "enabled": True,
                "tasks": [{
                    "name": "webhook",
                    "event": "task_complete",
                    "type": "http",
                    "url": "http://example.com/hook",
                    "method": "POST",
                    "payload_template": '{"task": "{{task_id}}"}',
                    "mode": "sync",
                    "timeout": 5,
                }],
            }
            loader = HookLoader(self.tmp)
            loader.load()

        event = HookEvent(
            event_type="task_complete",
            data={"task_id": "T007"},
        )
        handlers = loader.registry.get_handlers_for_event("task_complete")
        assert len(handlers) == 1

        with patch("requests.request") as mock_req:
            mock_req.return_value.status_code = 200
            mock_req.return_value.text = "ok"
            result = handlers[0].handler(event)
            call_kwargs = mock_req.call_args
            assert call_kwargs[1]["method"] == "POST"
            assert call_kwargs[1]["url"] == "http://example.com/hook"
            assert "T007" in call_kwargs[1]["data"]
