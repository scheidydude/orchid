"""Hook configuration schema for Orchid V2.

Defines Pydantic models for validating hook configurations in .orchid.yaml.

Usage:
    from orchid.hooks.schema import validate_hooks_config

    config = load_yaml(".orchid.yaml")
    validated = validate_hooks_config(config.get("hooks", {}))
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ── Constants ────────────────────────────────────────────────────────────────

VALID_EVENT_TYPES = [
    # Task lifecycle events
    "task_start",
    "task_end",
    "task_complete",
    "task_failed",
    "task_blocked",
    "task_skipped",
    "task_status_change",
    # Agent ReAct loop events
    "agent_iter_start",
    "agent_iter_end",
    "agent_action",
    "agent_observation",
    "agent_thought",
    "agent_final_answer",
    # Session and phase transition events
    "session_start",
    "session_end",
    "phase_transition",
    "phase_enter",
    "phase_exit",
    # Hook system events
    "hook_registered",
    "hook_unregistered",
    "hook_error",
]

VALID_HOOK_TYPES = ["shell", "http", "python"]

VALID_EXECUTION_MODES = ["sync", "async", "background"]

# Built-in shell commands that are always allowed (in addition to configured allowlist)
BUILTIN_SHELL_ALLOWLIST = [
    "echo",
    "printf",
    "date",
    "whoami",
    "hostname",
    "pwd",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "cut",
    "sort",
    "uniq",
    "tr",
    "sed",
    "awk",
    "test",
    "true",
    "false",
    "exit",
    "read",
    "set",
    "unset",
    "export",
    "alias",
    "type",
    "help",
    "source",
    ".",
    ":",
    "return",
    "break",
    "continue",
    "shift",
    "eval",
    "exec",
    "wait",
    "kill",
    "jobs",
    "bg",
    "fg",
    "disown",
    "times",
    "ulimit",
    "umask",
    "bind",
    "builtin",
    "caller",
    "command",
    "declare",
    "dirs",
    "enable",
    "let",
    "local",
    "logout",
    "mapfile",
    "popd",
    "pushd",
    "readarray",
    "readonly",
    "shopt",
    "suspend",
    "typeset",
    "unalias",
    "unset",
]


# ── Base Hook Schema ────────────────────────────────────────────────────────

class BaseHookSchema(BaseModel):
    """Base schema for all hook types."""

    name: str = Field(..., description="Unique name for this hook")
    event: str = Field(..., description="Event type to listen for")
    type: Literal["shell", "http", "python"] = Field(..., description="Hook type")
    mode: Literal["sync", "async", "background"] = Field(
        default="sync",
        description="Execution mode: sync (blocking), async (non-blocking), background (fire-and-forget)"
    )
    timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Timeout in seconds (1-300)"
    )

    @field_validator("event")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        """Validate event type is known."""
        if v not in VALID_EVENT_TYPES:
            raise ValueError(
                f"Unknown event type '{v}'. "
                f"Valid types: {', '.join(VALID_EVENT_TYPES)}"
            )
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        """Validate execution mode."""
        if v not in VALID_EXECUTION_MODES:
            raise ValueError(
                f"Unknown mode '{v}'. Valid modes: {', '.join(VALID_EXECUTION_MODES)}"
            )
        return v


# ── Shell Hook Schema ───────────────────────────────────────────────────────

class ShellHookSchema(BaseModel):
    """Schema for shell command hooks."""

    name: str = Field(..., description="Unique name for this hook")
    event: str = Field(..., description="Event type to listen for")
    type: Literal["shell"] = Field(default="shell", description="Hook type")
    command: str = Field(..., description="Shell command to execute")
    mode: Literal["sync", "async", "background"] = Field(
        default="sync",
        description="Execution mode"
    )
    timeout: int = Field(
        default=60,
        ge=1,
        le=300,
        description="Timeout in seconds"
    )
    allowlist_check: bool = Field(
        default=True,
        description="Whether to check command against allowlist"
    )

    @field_validator("event")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        if v not in VALID_EVENT_TYPES:
            raise ValueError(
                f"Unknown event type '{v}'. "
                f"Valid types: {', '.join(VALID_EVENT_TYPES)}"
            )
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in VALID_EXECUTION_MODES:
            raise ValueError(
                f"Unknown mode '{v}'. Valid modes: {', '.join(VALID_EXECUTION_MODES)}"
            )
        return v

    @field_validator("command")
    @classmethod
    def validate_command_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Command cannot be empty")
        return v.strip()


# ── HTTP Hook Schema ────────────────────────────────────────────────────────

class HTTPHookSchema(BaseModel):
    """Schema for HTTP request hooks."""

    name: str = Field(..., description="Unique name for this hook")
    event: str = Field(..., description="Event type to listen for")
    type: Literal["http"] = Field(default="http", description="Hook type")
    url: str = Field(..., description="URL to send request to")
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"] = Field(
        default="POST",
        description="HTTP method"
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="HTTP headers"
    )
    payload_template: str | None = Field(
        default=None,
        description="Request body template with variable substitution"
    )
    mode: Literal["sync", "async", "background"] = Field(
        default="async",
        description="Execution mode"
    )
    timeout: int = Field(
        default=10,
        ge=1,
        le=60,
        description="Timeout in seconds"
    )

    @field_validator("event")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        if v not in VALID_EVENT_TYPES:
            raise ValueError(
                f"Unknown event type '{v}'. "
                f"Valid types: {', '.join(VALID_EVENT_TYPES)}"
            )
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in VALID_EXECUTION_MODES:
            raise ValueError(
                f"Unknown mode '{v}'. Valid modes: {', '.join(VALID_EXECUTION_MODES)}"
            )
        return v

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("URL cannot be empty")
        return v.strip()


# ── Python Hook Schema ──────────────────────────────────────────────────────

class PythonHookSchema(BaseModel):
    """Schema for Python callback hooks."""

    name: str = Field(..., description="Unique name for this hook")
    event: str = Field(..., description="Event type to listen for")
    type: Literal["python"] = Field(default="python", description="Hook type")
    module: str = Field(..., description="Python module path (e.g., myproject.hooks)")
    function: str = Field(..., description="Function name within module")
    mode: Literal["sync", "async", "background"] = Field(
        default="sync",
        description="Execution mode"
    )
    timeout: int = Field(
        default=30,
        ge=1,
        le=120,
        description="Timeout in seconds"
    )

    @field_validator("event")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        if v not in VALID_EVENT_TYPES:
            raise ValueError(
                f"Unknown event type '{v}'. "
                f"Valid types: {', '.join(VALID_EVENT_TYPES)}"
            )
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in VALID_EXECUTION_MODES:
            raise ValueError(
                f"Unknown mode '{v}'. Valid modes: {', '.join(VALID_EXECUTION_MODES)}"
            )
        return v

    @field_validator("module")
    @classmethod
    def validate_module(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Module path cannot be empty")
        return v.strip()

    @field_validator("function")
    @classmethod
    def validate_function(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Function name cannot be empty")
        return v.strip()


# ── Hooks Configuration Schema ──────────────────────────────────────────────

class HooksConfigSchema(BaseModel):
    """Complete hooks configuration schema."""

    enabled: bool = Field(
        default=False,
        description="Whether hooks are enabled for this project"
    )
    shell_allowlist: list[str] = Field(
        default_factory=list,
        description="Additional shell commands allowed beyond built-in safe list"
    )
    tasks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Task lifecycle hooks"
    )
    phases: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Phase transition hooks"
    )
    agent: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Agent ReAct loop hooks"
    )
    session: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Session lifecycle hooks"
    )

    @model_validator(mode="after")
    def validate_hook_sections(self) -> HooksConfigSchema:
        """Validate all hook sections have valid hook configurations."""
        sections = ["tasks", "phases", "agent", "session"]
        for section in sections:
            hooks = getattr(self, section)
            if not isinstance(hooks, list):
                raise ValueError(f"Section '{section}' must be a list of hooks")
            for i, hook in enumerate(hooks):
                if not isinstance(hook, dict):
                    raise ValueError(
                        f"Hook at index {i} in '{section}' must be a dictionary"
                    )
                if "name" not in hook:
                    raise ValueError(
                        f"Hook at index {i} in '{section}' must have a 'name' field"
                    )
                if "event" not in hook:
                    raise ValueError(
                        f"Hook '{hook.get('name', '?')}' in '{section}' must have an 'event' field"
                    )
                if "type" not in hook:
                    raise ValueError(
                        f"Hook '{hook.get('name', '?')}' in '{section}' must have a 'type' field"
                    )
                hook_type = hook.get("type")
                if hook_type not in VALID_HOOK_TYPES:
                    raise ValueError(
                        f"Hook '{hook.get('name', '?')}' has invalid type '{hook_type}'. "
                        f"Valid types: {', '.join(VALID_HOOK_TYPES)}"
                    )
        return self

    def get_all_hooks(self) -> list[dict[str, Any]]:
        """Get all hooks from all sections as a flat list."""
        all_hooks = []
        for section in ["tasks", "phases", "agent", "session"]:
            section_hooks = getattr(self, section)
            for hook in section_hooks:
                hook_with_section = hook.copy()
                hook_with_section["_section"] = section
                all_hooks.append(hook_with_section)
        return all_hooks


# ── Validation Functions ────────────────────────────────────────────────────

def validate_hooks_config(config: dict[str, Any]) -> HooksConfigSchema:
    """Validate a hooks configuration dictionary.

    Args:
        config: Raw hooks configuration from .orchid.yaml

    Returns:
        Validated HooksConfigSchema

    Raises:
        ValueError: If configuration is invalid
    """
    try:
        return HooksConfigSchema(**config)
    except Exception as e:
        raise ValueError(f"Invalid hooks configuration: {e}")


def validate_hook(hook_config: dict[str, Any]) -> ShellHookSchema | HTTPHookSchema | PythonHookSchema:
    """Validate a single hook configuration.

    Args:
        hook_config: Single hook configuration dictionary

    Returns:
        Validated hook schema (type-specific)

    Raises:
        ValueError: If hook configuration is invalid
    """
    hook_type = hook_config.get("type")

    if hook_type == "shell":
        try:
            return ShellHookSchema(**hook_config)
        except Exception as e:
            raise ValueError(f"Invalid shell hook configuration: {e}")
    elif hook_type == "http":
        try:
            return HTTPHookSchema(**hook_config)
        except Exception as e:
            raise ValueError(f"Invalid HTTP hook configuration: {e}")
    elif hook_type == "python":
        try:
            return PythonHookSchema(**hook_config)
        except Exception as e:
            raise ValueError(f"Invalid Python hook configuration: {e}")
    else:
        raise ValueError(f"Unknown hook type: {hook_type}. Valid types: {', '.join(VALID_HOOK_TYPES)}")


def validate_shell_command(command: str, allowlist: list[str]) -> tuple[bool, str]:
    """Validate a shell command against the allowlist.

    Args:
        command: Shell command to validate
        allowlist: Configured allowlist of commands

    Returns:
        Tuple of (is_allowed, reason)
    """
    # Extract base command
    base_cmd = command.split()[0] if command else ""

    # Check against built-in allowlist
    if base_cmd in BUILTIN_SHELL_ALLOWLIST:
        return True, "Built-in safe command"

    # Check against configured allowlist
    combined_allowlist = BUILTIN_SHELL_ALLOWLIST + allowlist
    if base_cmd in combined_allowlist:
        return True, "Allowed by configuration"

    # Check prefix matches
    for allowed in combined_allowlist:
        if command.startswith(allowed):
            return True, f"Prefix matches allowed command: {allowed}"

    return False, f"Command '{base_cmd}' not in allowlist"


# ── Schema Documentation ────────────────────────────────────────────────────

def get_schema_documentation() -> str:
    """Return schema documentation as a string."""
    return """
# Orchid Hooks Configuration Schema

## Top-Level Structure

```yaml
hooks:
  enabled: true|false              # Enable/disable hooks for project
  shell_allowlist: []              # Additional allowed shell commands
  tasks: []                        # Task lifecycle hooks
  phases: []                       # Phase transition hooks
  agent: []                        # Agent loop hooks
  session: []                      # Session hooks
```

## Hook Fields (All Types)

| Field | Required | Type | Default | Description |
|-------|----------|------|---------|-------------|
| name | Yes | string | - | Unique hook name |
| event | Yes | string | - | Event type to listen for |
| type | Yes | string | - | Hook type: shell, http, python |
| mode | No | string | sync | Execution mode: sync, async, background |
| timeout | No | integer | 30-60 | Timeout in seconds |

## Event Types

### Task Lifecycle
- task_start - Task execution begins
- task_end - Task execution ends (any outcome)
- task_complete - Task completed successfully
- task_failed - Task failed
- task_blocked - Task blocked by dependency
- task_skipped - Task was skipped
- task_status_change - Task status changed

### Agent ReAct Loop
- agent_iter_start - New iteration begins
- agent_iter_end - Iteration completes
- agent_action - Agent takes an action
- agent_observation - Agent receives observation
- agent_thought - Agent produces thought
- agent_final_answer - Agent produces final answer

### Session & Phase
- session_start - Session loaded
- session_end - Session closed
- phase_transition - Phase changed
- phase_enter - Entered new phase
- phase_exit - Exited current phase

## Shell Hook Specific Fields

```yaml
- name: notify_task
  event: task_complete
  type: shell
  command: echo "Task {{task_id}} completed"
  mode: background
  timeout: 60
  allowlist_check: true
```

| Field | Required | Type | Default | Description |
|-------|----------|------|---------|-------------|
| command | Yes | string | - | Shell command to execute |
| allowlist_check | No | boolean | true | Check against allowlist |

## HTTP Hook Specific Fields

```yaml
- name: slack_notification
  event: task_complete
  type: http
  url: https://hooks.slack.com/services/xxx
  method: POST
  headers:
    Content-Type: application/json
  payload_template: |
    {"text": "Task {{task_id}} completed"}
  mode: async
  timeout: 10
```

| Field | Required | Type | Default | Description |
|-------|----------|------|---------|-------------|
| url | Yes | string | - | Request URL |
| method | No | string | POST | HTTP method |
| headers | No | object | {} | Request headers |
| payload_template | No | string | - | Request body template |

## Python Hook Specific Fields

```yaml
- name: custom_handler
  event: phase_transition
  type: python
  module: myproject.hooks
  function: on_phase_change
  mode: sync
  timeout: 30
```

| Field | Required | Type | Default | Description |
|-------|----------|------|---------|-------------|
| module | Yes | string | - | Python module path |
| function | Yes | string | - | Function name |

## Variable Substitution

Templates support these variables:
- {{task_id}} - Task identifier
- {{title}} - Task title
- {{result}} - Task result
- {{from_phase}} - Previous phase
- {{to_phase}} - New phase
- {{project_name}} - Project name
- {{action}} - Agent action
- {{input}} - Action input
- {{observation}} - Agent observation
- {{timestamp}} - ISO timestamp
- {{event_type}} - Event type string
- {{event_data}} - JSON of event data
- {{context.key}} - Context data by key
"""