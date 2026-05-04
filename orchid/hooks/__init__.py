"""Hook system for Orchid V2 - extensible event-driven hooks.

T096: Hooks are wired into the task lifecycle at the following points:
- task_start: Before task execution begins
- task_complete: After successful task completion
- task_failed: When task fails or is blocked
- agent_action/observation: During ReAct loop iterations

T097: Hooks are wired into session and phase transitions:
- session_start: When session.load() is called
- session_end: When session.close() is called
- phase_transition: When a phase change is initiated
- phase_enter: After successfully entering a new phase
- phase_exit: Before exiting the current phase

Usage:
    # In .orchid.yaml:
    hooks:
      enabled: true
      tasks:
        - name: notify_on_task_start
          event: task_start
          type: shell
          command: echo "Task started: {{task_id}}"
          mode: background

        - name: slack_notification
          event: task_complete
          type: http
          url: https://hooks.slack.com/...
          method: POST
          payload_template: |
            {
              "text": "Task {{task_id}} completed"
            }
          mode: async
"""

from orchid.hooks.events import (
    HookEvent,
    # Agent ReAct loop events
    AGENT_ITER_START,
    AGENT_ITER_END,
    AGENT_ACTION,
    AGENT_OBSERVATION,
    AGENT_THOUGHT,
    AGENT_FINAL_ANSWER,
    PRE_TOOL_USE,
    POST_TOOL_USE,
    DELEGATION_START,
    DELEGATION_END,
    # Task lifecycle events
    TASK_START,
    TASK_END,
    TASK_COMPLETE,
    TASK_FAILED,
    TASK_BLOCKED,
    TASK_SKIPPED,
    TASK_STATUS_CHANGE,
    # Session and phase transition events
    SESSION_START,
    SESSION_END,
    PHASE_TRANSITION,
    PHASE_ENTER,
    PHASE_EXIT,
    # Hook system events
    HOOK_REGISTERED,
    HOOK_UNREGISTERED,
    HOOK_ERROR,
    # Typed context dataclasses
    PreToolUseContext,
    PostToolUseContext,
    TaskStartContext,
    TaskEndContext,
    SessionStartContext,
    SessionEndContext,
    PhaseTransitionContext,
    DelegationContext,
)
from orchid.hooks.loader import HookLoadError, HookLoader
from orchid.hooks.registry import HookRegistry, HookResult
from orchid.hooks.types import HookCategory, HookExecutionMode, ShellHook, HTTPHook, PythonHook
from orchid.hooks.schema import (
    # Schema classes
    HooksConfigSchema,
    ShellHookSchema,
    HTTPHookSchema,
    PythonHookSchema,
    # Constants
    VALID_EVENT_TYPES,
    VALID_HOOK_TYPES,
    VALID_EXECUTION_MODES,
    BUILTIN_SHELL_ALLOWLIST,
    # Validation functions
    validate_hooks_config,
    validate_hook,
    validate_shell_command,
    get_schema_documentation,
)


def orchid_hook(event: str):
    """Decorator: tag a function to be auto-registered as a hook handler.

    Usage in project hooks.py:
        from orchid.hooks import orchid_hook, TASK_COMPLETE

        @orchid_hook(TASK_COMPLETE)
        def on_task_done(hook_event):
            print(hook_event.data)
    """
    def decorator(fn):
        fn._orchid_hook_event = event
        return fn
    return decorator


__all__ = [
    # Decorator
    "orchid_hook",
    # Events
    "HookEvent",
    # Agent ReAct loop events
    "AGENT_ITER_START",
    "AGENT_ITER_END",
    "AGENT_ACTION",
    "AGENT_OBSERVATION",
    "AGENT_THOUGHT",
    "AGENT_FINAL_ANSWER",
    "PRE_TOOL_USE",
    "POST_TOOL_USE",
    "DELEGATION_START",
    "DELEGATION_END",
    # Task lifecycle events
    "TASK_START",
    "TASK_END",
    "TASK_COMPLETE",
    "TASK_FAILED",
    "TASK_BLOCKED",
    "TASK_SKIPPED",
    "TASK_STATUS_CHANGE",
    # Session and phase transition events
    "SESSION_START",
    "SESSION_END",
    "PHASE_TRANSITION",
    "PHASE_ENTER",
    "PHASE_EXIT",
    # Hook system events
    "HOOK_REGISTERED",
    "HOOK_UNREGISTERED",
    "HOOK_ERROR",
    # Typed context dataclasses
    "PreToolUseContext",
    "PostToolUseContext",
    "TaskStartContext",
    "TaskEndContext",
    "SessionStartContext",
    "SessionEndContext",
    "PhaseTransitionContext",
    "DelegationContext",
    # Loader, Registry, Result
    "HookLoadError",
    "HookLoader",
    "HookRegistry",
    "HookResult",
    # Types
    "HookCategory",
    "HookExecutionMode",
    "ShellHook",
    "HTTPHook",
    "PythonHook",
    # Schema
    "HooksConfigSchema",
    "ShellHookSchema",
    "HTTPHookSchema",
    "PythonHookSchema",
    "VALID_EVENT_TYPES",
    "VALID_HOOK_TYPES",
    "VALID_EXECUTION_MODES",
    "BUILTIN_SHELL_ALLOWLIST",
    "validate_hooks_config",
    "validate_hook",
    "validate_shell_command",
    "get_schema_documentation",
]