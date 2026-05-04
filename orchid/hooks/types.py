"""Hook type definitions for Orchid V2.

Defines hook categories and execution modes.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable


class HookCategory(Enum):
    """Categories of hooks based on their purpose."""
    TASK = "task"  # Task lifecycle hooks
    PHASE = "phase"  # Phase transition hooks
    AGENT = "agent"  # Agent ReAct loop hooks
    SESSION = "session"  # Session lifecycle hooks
    SYSTEM = "system"  # System-level hooks


class HookExecutionMode(Enum):
    """How hooks are executed."""
    SYNC = "sync"  # Blocking - waits for hook to complete
    ASYNC = "async"  # Non-blocking - fires and continues
    BACKGROUND = "background"  # Fire-and-forget, errors ignored


class HookType:
    """Base class for hook definitions."""

    def __init__(
        self,
        name: str,
        event_type: str,
        category: HookCategory,
        mode: HookExecutionMode = HookExecutionMode.SYNC,
        timeout: int = 30,
    ):
        self.name = name
        self.event_type = event_type
        self.category = category
        self.mode = mode
        self.timeout = timeout


class ShellHook(HookType):
    """Hook that executes a shell command."""

    def __init__(
        self,
        name: str,
        event_type: str,
        command: str,
        category: HookCategory = HookCategory.TASK,
        mode: HookExecutionMode = HookExecutionMode.SYNC,
        timeout: int = 60,
        allowlist_check: bool = True,
    ):
        super().__init__(name, event_type, category, mode, timeout)
        self.command = command
        self.allowlist_check = allowlist_check


class HTTPHook(HookType):
    """Hook that makes an HTTP request."""

    def __init__(
        self,
        name: str,
        event_type: str,
        url: str,
        method: str = "POST",
        headers: dict | None = None,
        payload_template: str | None = None,
        category: HookCategory = HookCategory.TASK,
        mode: HookExecutionMode = HookExecutionMode.ASYNC,
        timeout: int = 10,
    ):
        super().__init__(name, event_type, category, mode, timeout)
        self.url = url
        self.method = method
        self.headers = headers or {}
        self.payload_template = payload_template


class PythonHook(HookType):
    """Hook that executes a Python callable."""

    def __init__(
        self,
        name: str,
        event_type: str,
        callback: Callable,
        category: HookCategory = HookCategory.TASK,
        mode: HookExecutionMode = HookExecutionMode.SYNC,
    ):
        super().__init__(name, event_type, category, mode)
        self.callback = callback