"""Unit tests for the Orchid V2 hook system."""

import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchid.hooks.events import (
    HookEvent,
    AGENT_ITER_START,
    AGENT_ITER_END,
    AGENT_ACTION,
    AGENT_OBSERVATION,
    AGENT_THOUGHT,
    AGENT_FINAL_ANSWER,
    TASK_START,
    TASK_END,
    TASK_COMPLETE,
    TASK_FAILED,
    TASK_BLOCKED,
    TASK_SKIPPED,
    TASK_STATUS_CHANGE,
    SESSION_START,
    SESSION_END,
    PHASE_TRANSITION,
    PHASE_ENTER,
    PHASE_EXIT,
    HOOK_REGISTERED,
    HOOK_UNREGISTERED,
    HOOK_ERROR,
)
from orchid.hooks.registry import HookRegistry, HookHandler
from orchid.hooks.loader import HookLoader
from orchid.hooks.types import HookCategory, HookExecutionMode, ShellHook, HTTPHook, PythonHook
from orchid.hooks.schema import (
    HooksConfigSchema,
    ShellHookSchema,
    HTTPHookSchema,
    PythonHookSchema,
    VALID_EVENT_TYPES,
    VALID_HOOK_TYPES,
    VALID_EXECUTION_MODES,
    BUILTIN_SHELL_ALLOWLIST,
    validate_hooks_config,
    validate_hook,
    validate_shell_command,
    get_schema_documentation,
)


def _clear_registry():
    """Helper to clear the HookRegistry singleton."""
    registry = HookRegistry()
    registry.clear()


class TestHookEvent:
    """Tests for HookEvent dataclass."""

    def test_create_basic_event(self):
        """Test creating a basic HookEvent."""
        event = HookEvent(
            event_type="test_event",
            data={"key": "value"},
            context={"task_id": "T001"},
        )
        assert event.event_type == "test_event"
        assert event.data == {"key": "value"}
        assert event.context == {"task_id": "T001"}
        assert event.timestamp != ""

    def test_event_timestamp_auto_generated(self):
        """Test that timestamp is auto-generated if not provided."""
        event = HookEvent(event_type="test")
        assert event.timestamp != ""

    def test_task_start_event_factory(self):
        """Test TASK_START event factory method."""
        event = HookEvent.task_start_event(task_id="T001", title="Test Task")
        assert event.event_type == TASK_START
        assert event.data["task_id"] == "T001"
        assert event.data["title"] == "Test Task"
        assert event.context["task_id"] == "T001"

    def test_task_complete_event_factory(self):
        """Test TASK_COMPLETE event factory method."""
        event = HookEvent.task_complete_event(
            task_id="T001",
            result="Success",
            files=["file1.txt", "file2.txt"]
        )
        assert event.event_type == TASK_COMPLETE
        assert event.data["task_id"] == "T001"
        assert event.data["result"] == "Success"
        assert event.data["files_written"] == ["file1.txt", "file2.txt"]

    def test_task_failed_event_factory(self):
        """Test TASK_FAILED event factory method."""
        event = HookEvent.task_failed_event(
            task_id="T001",
            error="Something went wrong"
        )
        assert event.event_type == TASK_FAILED
        assert event.data["task_id"] == "T001"
        assert event.data["error"] == "Something went wrong"

    def test_phase_transition_event_factory(self):
        """Test PHASE_TRANSITION event factory method."""
        event = HookEvent.phase_transition_event(
            from_phase="PLANNING",
            to_phase="EXECUTING",
            project_name="test_project"
        )
        assert event.event_type == PHASE_TRANSITION
        assert event.data["from_phase"] == "PLANNING"
        assert event.data["to_phase"] == "EXECUTING"
        assert event.data["project_name"] == "test_project"

    def test_agent_action_event_factory(self):
        """Test AGENT_ACTION event factory method."""
        event = HookEvent.agent_action_event(
            task_id="T001",
            action="write_file",
            input_data="content",
            iteration=1
        )
        assert event.event_type == AGENT_ACTION
        assert event.data["task_id"] == "T001"
        assert event.data["action"] == "write_file"
        assert event.data["input"] == "content"
        assert event.data["iteration"] == 1

    def test_agent_observation_event_factory(self):
        """Test AGENT_OBSERVATION event factory method."""
        event = HookEvent.agent_observation_event(
            task_id="T001",
            action="write_file",
            observation="File written successfully",
            error=False
        )
        assert event.event_type == AGENT_OBSERVATION
        assert event.data["task_id"] == "T001"
        assert event.data["observation"] == "File written successfully"
        assert event.data["error"] is False


class TestEventConstants:
    """Tests for event type string constants."""

    def test_agent_event_constants(self):
        """Test agent loop event constants."""
        assert AGENT_ITER_START == "agent_iter_start"
        assert AGENT_ITER_END == "agent_iter_end"
        assert AGENT_ACTION == "agent_action"
        assert AGENT_OBSERVATION == "agent_observation"
        assert AGENT_THOUGHT == "agent_thought"
        assert AGENT_FINAL_ANSWER == "agent_final_answer"

    def test_task_event_constants(self):
        """Test task lifecycle event constants."""
        assert TASK_START == "task_start"
        assert TASK_END == "task_end"
        assert TASK_COMPLETE == "task_complete"
        assert TASK_FAILED == "task_failed"
        assert TASK_BLOCKED == "task_blocked"
        assert TASK_SKIPPED == "task_skipped"
        assert TASK_STATUS_CHANGE == "task_status_change"

    def test_session_event_constants(self):
        """Test session and phase event constants."""
        assert SESSION_START == "session_start"
        assert SESSION_END == "session_end"
        assert PHASE_TRANSITION == "phase_transition"
        assert PHASE_ENTER == "phase_enter"
        assert PHASE_EXIT == "phase_exit"

    def test_hook_system_event_constants(self):
        """Test hook system event constants."""
        assert HOOK_REGISTERED == "hook_registered"
        assert HOOK_UNREGISTERED == "hook_unregistered"
        assert HOOK_ERROR == "hook_error"


class TestHookTypes:
    """Tests for HookCategory and HookExecutionMode enums."""

    def test_hook_category_values(self):
        """Test HookCategory enum values."""
        assert HookCategory.TASK.value == "task"
        assert HookCategory.PHASE.value == "phase"
        assert HookCategory.AGENT.value == "agent"
        assert HookCategory.SESSION.value == "session"
        assert HookCategory.SYSTEM.value == "system"

    def test_hook_execution_mode_values(self):
        """Test HookExecutionMode enum values."""
        assert HookExecutionMode.SYNC.value == "sync"
        assert HookExecutionMode.ASYNC.value == "async"
        assert HookExecutionMode.BACKGROUND.value == "background"


class TestShellHook:
    """Tests for ShellHook class."""

    def test_create_shell_hook(self):
        """Test creating a ShellHook."""
        hook = ShellHook(
            name="test_hook",
            event_type="task_start",
            command="echo hello",
            category=HookCategory.TASK,
            mode=HookExecutionMode.SYNC,
            timeout=60,
            allowlist_check=True,
        )
        assert hook.name == "test_hook"
        assert hook.event_type == "task_start"
        assert hook.command == "echo hello"
        assert hook.category == HookCategory.TASK
        assert hook.mode == HookExecutionMode.SYNC
        assert hook.timeout == 60
        assert hook.allowlist_check is True

    def test_shell_hook_defaults(self):
        """Test ShellHook default values."""
        hook = ShellHook(
            name="test",
            event_type="task_start",
            command="echo test"
        )
        assert hook.category == HookCategory.TASK
        assert hook.mode == HookExecutionMode.SYNC
        assert hook.timeout == 60
        assert hook.allowlist_check is True


class TestHTTPHook:
    """Tests for HTTPHook class."""

    def test_create_http_hook(self):
        """Test creating an HTTPHook."""
        hook = HTTPHook(
            name="slack_notify",
            event_type="task_complete",
            url="https://hooks.slack.com/test",
            method="POST",
            headers={"Content-Type": "application/json"},
            payload_template='{"text": "Task done"}',
            category=HookCategory.TASK,
            mode=HookExecutionMode.ASYNC,
            timeout=10,
        )
        assert hook.name == "slack_notify"
        assert hook.event_type == "task_complete"
        assert hook.url == "https://hooks.slack.com/test"
        assert hook.method == "POST"
        assert hook.headers == {"Content-Type": "application/json"}
        assert hook.payload_template == '{"text": "Task done"}'
        assert hook.category == HookCategory.TASK
        assert hook.mode == HookExecutionMode.ASYNC
        assert hook.timeout == 10

    def test_http_hook_defaults(self):
        """Test HTTPHook default values."""
        hook = HTTPHook(
            name="test",
            event_type="task_complete",
            url="http://example.com"
        )
        assert hook.method == "POST"
        assert hook.headers == {}
        assert hook.payload_template is None
        assert hook.category == HookCategory.TASK
        assert hook.mode == HookExecutionMode.ASYNC
        assert hook.timeout == 10


class TestPythonHook:
    """Tests for PythonHook class."""

    def test_create_python_hook(self):
        """Test creating a PythonHook."""
        def my_callback(event):
            return "handled"

        hook = PythonHook(
            name="custom_handler",
            event_type="phase_transition",
            callback=my_callback,
            category=HookCategory.PHASE,
            mode=HookExecutionMode.SYNC,
        )
        assert hook.name == "custom_handler"
        assert hook.event_type == "phase_transition"
        assert hook.callback == my_callback
        assert hook.category == HookCategory.PHASE
        assert hook.mode == HookExecutionMode.SYNC

    def test_python_hook_defaults(self):
        """Test PythonHook default values."""
        def my_callback(event):
            pass

        hook = PythonHook(
            name="test",
            event_type="phase_transition",
            callback=my_callback
        )
        assert hook.category == HookCategory.TASK
        assert hook.mode == HookExecutionMode.SYNC


class TestHookRegistry:
    """Tests for HookRegistry singleton."""

    def setup_method(self):
        """Clear registry before each test."""
        self.registry = HookRegistry()
        self.registry.clear()

    def teardown_method(self):
        """Clear registry after each test."""
        self.registry.clear()

    def test_singleton_pattern(self):
        """Test that HookRegistry is a singleton."""
        registry1 = HookRegistry()
        registry2 = HookRegistry()
        assert registry1 is registry2

    def test_register_handler(self):
        """Test registering a handler."""
        handler = lambda e: None
        handler_id = self.registry.register(
            event_type="test_event",
            handler=handler,
            priority=10,
            mode="sync",
            timeout=30,
        )
        assert handler_id is not None
        assert len(self.registry._registered) == 1
        assert len(self.registry._handlers["test_event"]) == 1

    def test_register_multiple_handlers(self):
        """Test registering multiple handlers for same event."""
        self.registry.register("test_event", lambda e: "handler1", priority=10)
        self.registry.register("test_event", lambda e: "handler2", priority=20)
        self.registry.register("test_event", lambda e: "handler3", priority=15)

        handlers = self.registry._handlers["test_event"]
        assert len(handlers) == 3
        assert handlers[0].priority == 20
        assert handlers[1].priority == 15
        assert handlers[2].priority == 10

    def test_unregister_handler(self):
        """Test unregistering a handler."""
        handler_id = self.registry.register("test_event", lambda e: None)
        result = self.registry.unregister(handler_id)
        assert result is True
        assert len(self.registry._registered) == 0
        assert len(self.registry._handlers["test_event"]) == 0

    def test_unregister_nonexistent_handler(self):
        """Test unregistering a handler that does not exist."""
        result = self.registry.unregister("nonexistent-id")
        assert result is False

    def test_fire_event_sync(self):
        """Test firing sync event handlers."""
        results = []

        def handler1(event):
            results.append("handler1")
            return "result1"

        def handler2(event):
            results.append("handler2")
            return "result2"

        self.registry.register("test_event", handler1, priority=10)
        self.registry.register("test_event", handler2, priority=20)

        event = HookEvent(event_type="test_event", data={"test": "data"})
        results_from_fire = self.registry.fire(event, ignore_errors=True)

        assert results == ["handler2", "handler1"]
        assert results_from_fire == ["result2", "result1"]

    def test_fire_event_no_handlers(self):
        """Test firing event with no handlers."""
        event = HookEvent(event_type="nonexistent_event")
        results = self.registry.fire(event)
        assert results == []

    def test_fire_event_background_mode(self):
        """Test firing background mode handlers."""
        results = []
        lock = threading.Lock()

        def background_handler(event):
            with lock:
                results.append("background")

        self.registry.register("test_event", background_handler, mode="background")

        event = HookEvent(event_type="test_event")
        self.registry.fire(event, ignore_errors=True)

        time.sleep(0.1)
        assert "background" in results

    def test_fire_event_async_mode(self):
        """Test firing async mode handlers."""
        results = []
        lock = threading.Lock()

        def async_handler(event):
            with lock:
                results.append("async")

        self.registry.register("test_event", async_handler, mode="async")

        event = HookEvent(event_type="test_event")
        self.registry.fire(event, ignore_errors=True)

        time.sleep(0.1)
        assert "async" in results

    def test_fire_event_error_handling(self):
        """Test that handler errors do not crash the fire loop."""
        def good_handler(event):
            return "success"

        def bad_handler(event):
            raise Exception("Handler error")

        self.registry.register("test_event", good_handler, priority=10)
        self.registry.register("test_event", bad_handler, priority=20)

        event = HookEvent(event_type="test_event")
        results = self.registry.fire(event, ignore_errors=True)

        assert "success" in results

    def test_get_handlers_for_event(self):
        """Test getting handlers for an event type."""
        self.registry.register("test_event", lambda e: None)
        self.registry.register("other_event", lambda e: None)

        handlers = self.registry.get_handlers_for_event("test_event")
        assert len(handlers) == 1

        handlers = self.registry.get_handlers_for_event("other_event")
        assert len(handlers) == 1

        handlers = self.registry.get_handlers_for_event("nonexistent")
        assert len(handlers) == 0

    def test_clear_registry(self):
        """Test clearing all handlers."""
        self.registry.register("event1", lambda e: None)
        self.registry.register("event2", lambda e: None)

        self.registry.clear()

        assert len(self.registry._handlers) == 0
        assert len(self.registry._registered) == 0

    def test_registered_count(self):
        """Test getting total registered handler count."""
        self.registry.register("event1", lambda e: None)
        self.registry.register("event1", lambda e: None)
        self.registry.register("event2", lambda e: None)

        assert self.registry.registered_count == 3


class TestHookHandler:
    """Tests for HookHandler class."""

    def test_create_handler(self):
        """Test creating a HookHandler."""
        handler = HookHandler(
            id="test-id",
            event_type="test_event",
            handler=lambda e: None,
            priority=10,
            mode="sync",
            timeout=30,
        )
        assert handler.id == "test-id"
        assert handler.event_type == "test_event"
        assert handler.priority == 10
        assert handler.mode == "sync"
        assert handler.timeout == 30

    def test_handler_repr(self):
        """Test HookHandler string representation."""
        handler = HookHandler(
            id="test-id",
            event_type="test_event",
            handler=lambda e: None,
            priority=10,
            mode="sync",
            timeout=30,
        )
        repr_str = repr(handler)
        assert "test-id" in repr_str
        assert "test_event" in repr_str
        assert "sync" in repr_str
        assert "10" in repr_str


class TestHookLoader:
    """Tests for HookLoader class."""

    def setup_method(self):
        """Set up test fixtures."""
        _clear_registry()
        self.temp_dir = tempfile.mkdtemp()
        self.project_path = Path(self.temp_dir)

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_loader_initialization(self):
        """Test HookLoader initialization."""
        loader = HookLoader(self.project_path)
        assert loader.project_dir == self.project_path
        assert isinstance(loader.registry, HookRegistry)
        assert loader._loaded_hooks == []
        assert loader._section_counts == {}

    def test_load_with_disabled_hooks(self):
        """Test loading when hooks are disabled."""
        with patch('orchid.config.get') as mock_get:
            mock_get.return_value = {"enabled": False}
            loader = HookLoader(self.project_path)
            count = loader.load()
        assert count == 0

    def test_load_with_empty_config(self):
        """Test loading with empty hooks config."""
        with patch('orchid.config.get') as mock_get:
            mock_get.return_value = {"enabled": True}
            loader = HookLoader(self.project_path)
            count = loader.load()
        assert count == 0

    def test_load_shell_hook(self):
        """Test loading a shell hook from config."""
        with patch('orchid.config.get') as mock_get:
            mock_get.return_value = {
                "enabled": True,
                "tasks": [
                    {
                        "name": "test_shell_hook",
                        "event": "task_start",
                        "type": "shell",
                        "command": "echo 'Task started'",
                        "mode": "sync",
                        "timeout": 60,
                    }
                ],
            }
            loader = HookLoader(self.project_path)
            count = loader.load()
        assert count == 1
        handlers = loader.registry.get_handlers_for_event("task_start")
        assert len(handlers) == 1

    def test_load_http_hook(self):
        """Test loading an HTTP hook from config."""
        with patch('orchid.config.get') as mock_get:
            mock_get.return_value = {
                "enabled": True,
                "tasks": [
                    {
                        "name": "test_http_hook",
                        "event": "task_complete",
                        "type": "http",
                        "url": "http://example.com/webhook",
                        "method": "POST",
                        "mode": "async",
                        "timeout": 10,
                    }
                ],
            }
            loader = HookLoader(self.project_path)
            count = loader.load()
        assert count == 1
        handlers = loader.registry.get_handlers_for_event("task_complete")
        assert len(handlers) == 1

    def test_load_multiple_sections(self):
        """Test loading hooks from multiple sections."""
        with patch('orchid.config.get') as mock_get:
            mock_get.return_value = {
                "enabled": True,
                "tasks": [
                    {"name": "task_hook", "event": "task_start", "type": "shell", "command": "echo test"}
                ],
                "phases": [
                    {"name": "phase_hook", "event": "phase_transition", "type": "shell", "command": "echo test"}
                ],
                "agent": [
                    {"name": "agent_hook", "event": "agent_action", "type": "shell", "command": "echo test"}
                ],
                "session": [
                    {"name": "session_hook", "event": "session_start", "type": "shell", "command": "echo test"}
                ],
            }
            loader = HookLoader(self.project_path)
            count = loader.load()
        assert count == 4
        assert loader._section_counts["tasks"] == 1
        assert loader._section_counts["phases"] == 1
        assert loader._section_counts["agent"] == 1
        assert loader._section_counts["session"] == 1

    def test_substitute_vars(self):
        """Test variable substitution in templates."""
        loader = HookLoader(self.project_path)
        event = HookEvent(
            event_type="task_start",
            data={
                "task_id": "T001",
                "title": "Test Task",
                "result": "Success",
            },
            context={"phase": "EXECUTING"},
            timestamp="2024-01-01T00:00:00Z",
        )
        template = "Task {{task_id}} ({{title}}) completed with {{result}} at {{timestamp}}"
        result = loader._substitute_vars(template, event)
        assert "T001" in result
        assert "Test Task" in result
        assert "Success" in result
        assert "2024-01-01T00:00:00Z" in result

    def test_substitute_context_vars(self):
        """Test context variable substitution."""
        loader = HookLoader(self.project_path)
        event = HookEvent(
            event_type="phase_transition",
            data={},
            context={"phase": "EXECUTING", "project": "myproject"},
        )
        template = "Phase: {{context.phase}}, Project: {{context.project}}"
        result = loader._substitute_vars(template, event)
        assert "EXECUTING" in result
        assert "myproject" in result

    def test_substitute_event_data_json(self):
        """Test JSON event data substitution."""
        loader = HookLoader(self.project_path)
        event = HookEvent(
            event_type="task_complete",
            data={"task_id": "T001", "status": "done"},
        )
        template = "Event data: {{event_data}}"
        result = loader._substitute_vars(template, event)
        assert "T001" in result
        assert "done" in result

    def test_is_command_allowed_empty_allowlist(self):
        """Test command allowlist check for built-in commands."""
        with patch('orchid.config.get') as mock_get:
            mock_get.return_value = []
            loader = HookLoader(self.project_path)
            assert loader._is_command_allowed("echo hello") is False
            assert loader._is_command_allowed("cat file.txt") is False
            assert loader._is_command_allowed("grep pattern file") is False

    def test_is_command_allowed_not_allowed(self):
        """Test command allowlist check for disallowed commands."""
        with patch('orchid.config.get') as mock_get:
            mock_get.return_value = []
            loader = HookLoader(self.project_path)
            assert loader._is_command_allowed("dangerous_command") is False
            assert loader._is_command_allowed("wget http://evil.com") is False

    def test_get_loaded_hooks(self):
        """Test getting list of loaded hook configurations."""
        with patch('orchid.config.get') as mock_get:
            hook_config = {
                "name": "test_hook",
                "event": "task_start",
                "type": "shell",
                "command": "echo test",
            }
            mock_get.return_value = {
                "enabled": True,
                "tasks": [hook_config],
            }
            loader = HookLoader(self.project_path)
            loader.load()
        loaded = loader.get_loaded_hooks()
        assert len(loaded) == 1
        assert loaded[0]["name"] == "test_hook"


class TestSchemaValidation:
    """Tests for schema validation functions."""

    def test_validate_hooks_config_valid(self):
        """Test validating a valid hooks configuration."""
        config = {
            "enabled": True,
            "shell_allowlist": ["mycommand"],
            "tasks": [
                {
                    "name": "test_hook",
                    "event": "task_start",
                    "type": "shell",
                    "command": "echo test",
                }
            ],
        }
        result = validate_hooks_config(config)
        assert result.enabled is True
        assert result.shell_allowlist == ["mycommand"]
        assert len(result.tasks) == 1

    def test_validate_hooks_config_invalid_hook_type(self):
        """Test validation rejects invalid hook type."""
        config = {
            "enabled": True,
            "tasks": [
                {
                    "name": "test_hook",
                    "event": "task_start",
                    "type": "invalid_type",
                    "command": "echo test",
                }
            ],
        }
        with pytest.raises(ValueError, match="invalid type"):
            validate_hooks_config(config)

    def test_validate_hooks_config_missing_name(self):
        """Test validation rejects hook without name."""
        config = {
            "enabled": True,
            "tasks": [
                {
                    "event": "task_start",
                    "type": "shell",
                    "command": "echo test",
                }
            ],
        }
        with pytest.raises(ValueError, match="must have a 'name' field"):
            validate_hooks_config(config)

    def test_validate_shell_hook_schema(self):
        """Test ShellHookSchema validation."""
        config = {
            "name": "test_shell",
            "event": "task_start",
            "type": "shell",
            "command": "echo hello",
            "mode": "sync",
            "timeout": 60,
        }
        result = ShellHookSchema(**config)
        assert result.name == "test_shell"
        assert result.command == "echo hello"

    def test_validate_shell_hook_empty_command(self):
        """Test ShellHookSchema rejects empty command."""
        config = {
            "name": "test_shell",
            "event": "task_start",
            "type": "shell",
            "command": "",
        }
        with pytest.raises(ValueError, match="cannot be empty"):
            ShellHookSchema(**config)

    def test_validate_http_hook_schema(self):
        """Test HTTPHookSchema validation."""
        config = {
            "name": "test_http",
            "event": "task_complete",
            "type": "http",
            "url": "http://example.com/webhook",
            "method": "POST",
            "mode": "async",
            "timeout": 10,
        }
        result = HTTPHookSchema(**config)
        assert result.name == "test_http"
        assert result.url == "http://example.com/webhook"
        assert result.method == "POST"

    def test_validate_http_hook_empty_url(self):
        """Test HTTPHookSchema rejects empty URL."""
        config = {
            "name": "test_http",
            "event": "task_complete",
            "type": "http",
            "url": "",
        }
        with pytest.raises(ValueError, match="URL cannot be empty"):
            HTTPHookSchema(**config)

    def test_validate_python_hook_schema(self):
        """Test PythonHookSchema validation."""
        config = {
            "name": "test_python",
            "event": "phase_transition",
            "type": "python",
            "module": "myproject.hooks",
            "function": "on_phase_change",
            "mode": "sync",
        }
        result = PythonHookSchema(**config)
        assert result.name == "test_python"
        assert result.module == "myproject.hooks"
        assert result.function == "on_phase_change"

    def test_validate_python_hook_empty_module(self):
        """Test PythonHookSchema rejects empty module."""
        config = {
            "name": "test_python",
            "event": "phase_transition",
            "type": "python",
            "module": "",
            "function": "on_phase_change",
        }
        with pytest.raises(ValueError, match="Module path cannot be empty"):
            PythonHookSchema(**config)

    def test_validate_hook_shell_type(self):
        """Test validate_hook for shell type."""
        config = {
            "name": "test",
            "event": "task_start",
            "type": "shell",
            "command": "echo test",
        }
        result = validate_hook(config)
        assert isinstance(result, ShellHookSchema)

    def test_validate_hook_http_type(self):
        """Test validate_hook for http type."""
        config = {
            "name": "test",
            "event": "task_complete",
            "type": "http",
            "url": "http://example.com",
        }
        result = validate_hook(config)
        assert isinstance(result, HTTPHookSchema)

    def test_validate_hook_python_type(self):
        """Test validate_hook for python type."""
        config = {
            "name": "test",
            "event": "phase_transition",
            "type": "python",
            "module": "myproject.hooks",
            "function": "handler",
        }
        result = validate_hook(config)
        assert isinstance(result, PythonHookSchema)

    def test_validate_hook_unknown_type(self):
        """Test validate_hook rejects unknown type."""
        config = {
            "name": "test",
            "event": "task_start",
            "type": "unknown",
        }
        with pytest.raises(ValueError, match="Unknown hook type"):
            validate_hook(config)

    def test_validate_shell_command_builtin(self):
        """Test validate_shell_command for built-in commands."""
        is_allowed, reason = validate_shell_command("echo hello", [])
        assert is_allowed is True
        assert "Built-in safe" in reason

    def test_validate_shell_command_custom_allowlist(self):
        """Test validate_shell_command with custom allowlist."""
        is_allowed, reason = validate_shell_command("mycommand arg1", ["mycommand"])
        assert is_allowed is True
        assert "Allowed" in reason

    def test_validate_shell_command_not_allowed(self):
        """Test validate_shell_command for disallowed command."""
        is_allowed, reason = validate_shell_command("dangerous_command", [])
        assert is_allowed is False
        assert "not in allowlist" in reason

    def test_validate_shell_command_prefix_match(self):
        """Test validate_shell_command prefix matching."""
        is_allowed, reason = validate_shell_command("mycommand extra args", ["mycommand"])
        assert is_allowed is True
        assert "Allowed" in reason


class TestSchemaConstants:
    """Tests for schema constants."""

    def test_valid_event_types(self):
        """Test VALID_EVENT_TYPES list."""
        assert "task_start" in VALID_EVENT_TYPES
        assert "task_complete" in VALID_EVENT_TYPES
        assert "task_failed" in VALID_EVENT_TYPES
        assert "agent_action" in VALID_EVENT_TYPES
        assert "phase_transition" in VALID_EVENT_TYPES
        assert "session_start" in VALID_EVENT_TYPES

    def test_valid_hook_types(self):
        """Test VALID_HOOK_TYPES list."""
        assert "shell" in VALID_HOOK_TYPES
        assert "http" in VALID_HOOK_TYPES
        assert "python" in VALID_HOOK_TYPES

    def test_valid_execution_modes(self):
        """Test VALID_EXECUTION_MODES list."""
        assert "sync" in VALID_EXECUTION_MODES
        assert "async" in VALID_EXECUTION_MODES
        assert "background" in VALID_EXECUTION_MODES

    def test_builtin_shell_allowlist(self):
        """Test BUILTIN_SHELL_ALLOWLIST contains common safe commands."""
        assert "echo" in BUILTIN_SHELL_ALLOWLIST
        assert "cat" in BUILTIN_SHELL_ALLOWLIST
        assert "grep" in BUILTIN_SHELL_ALLOWLIST
        assert "ls" not in BUILTIN_SHELL_ALLOWLIST


class TestSchemaDocumentation:
    """Tests for schema documentation."""

    def test_get_schema_documentation(self):
        """Test getting schema documentation."""
        doc = get_schema_documentation()
        assert "# Orchid Hooks Configuration Schema" in doc
        assert "## Top-Level Structure" in doc
        assert "## Hook Fields" in doc
        assert "## Event Types" in doc
        assert "## Shell Hook Specific Fields" in doc
        assert "## HTTP Hook Specific Fields" in doc
        assert "## Python Hook Specific Fields" in doc