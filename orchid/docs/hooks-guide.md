# Orchid Hook System Guide

The Orchid hook system provides extensible event-driven hooks for integrating with external systems, logging, notifications, and custom automation.

## Overview

Hooks can be fired at the following points in the Orchid lifecycle:

### Task Lifecycle (T096)
- `task_start` - Before task execution begins
- `task_complete` - After successful task completion  
- `task_failed` - When task fails or is blocked
- `task_blocked` - When task is blocked (pending dependencies)

### Agent ReAct Loop (T095)
- `agent_action` - When agent calls a tool
- `agent_observation` - After tool execution returns

### Session & Phase Transitions (T097)
- `session_start` - When session.load() is called
- `session_end` - When session.close() is called
- `phase_transition` - When a phase change is initiated
- `phase_enter` - After successfully entering a new phase

## Configuration

Hooks are configured in your project's `.orchid.yaml` file:

```yaml
hooks:
  enabled: true
  
  # Task lifecycle hooks
  tasks:
    - name: notify_on_task_start
      event: task_start
      type: shell
      command: echo "Task started: {{task_id}}"
      mode: background
      timeout: 10

    - name: slack_task_complete
      event: task_complete
      type: http
      url: https://hooks.slack.com/services/XXX
      method: POST
      payload_template: |
        {
          "text": "Task {{task_id}} completed: {{title}}"
        }
      mode: async
      timeout: 10

  # Phase transition hooks
  phases:
    - name: update_phase_marker
      event: phase_transition
      type: shell
      command: echo "{{to_phase}}" > .orchid/current_phase.txt
      mode: sync
      timeout: 5

  # Session hooks
  session:
    - name: log_session_start
      event: session_start
      type: python
      module: myproject.hooks
      function: on_session_start
      mode: sync
      timeout: 5
```

## Hook Types

### Shell Hooks

Execute shell commands with event data substitution:

```yaml
- name: notify_task
  event: task_complete
  type: shell
  command: |
    echo "Task {{task_id}} completed at {{timestamp}}" >> /var/log/orchid-tasks.log
  mode: background
  timeout: 10
```

**Variables available:**
- `{{task_id}}` - Task identifier
- `{{title}}` - Task title
- `{{type}}` - Task type
- `{{timestamp}}` - ISO timestamp
- `{{event_type}}` - Event type name
- `{{event_data}}` - JSON of all event data
- `{{context.<key>}}` - Context variables

### HTTP Hooks

Make HTTP requests with event data:

```yaml
- name: slack_notification
  event: task_complete
  type: http
  url: https://hooks.slack.com/services/XXX
  method: POST
  headers:
    Content-Type: application/json
  payload_template: |
    {
      "text": "✅ Task {{task_id}} completed",
      "attachments": [{
        "fields": [
          {"title": "Task", "value": "{{title}}", "short": true},
          {"title": "Type", "value": "{{type}}", "short": true}
        ]
      }]
    }
  mode: async
  timeout: 10
```

### Python Hooks

Call Python functions directly:

```yaml
- name: custom_handler
  event: task_complete
  type: python
  module: myproject.hooks
  function: on_task_complete
  mode: sync
  timeout: 5
```

The function receives a `HookEvent` object:

```python
from orchid.hooks.events import HookEvent

def on_task_complete(event: HookEvent) -> None:
    task_id = event.data.get("task_id")
    title = event.data.get("title")
    files = event.data.get("files_written", [])
    
    # Your custom logic here
    print(f"Task {task_id} completed: {title}")
```

## Execution Modes

- **sync** (default): Blocking - waits for hook to complete
- **async**: Non-blocking - fires in background thread with timeout
- **background**: Fire-and-forget - errors ignored, no waiting

## Shell Command Allowlist

Shell hooks are sandboxed by an allowlist. Commands must be in the allowlist:

```yaml
hooks:
  shell_allowlist:
    - echo
    - git
    - python
    - pytest
    - black
    - ruff
```

## Event Data Reference

### task_start
```python
{
    "task_id": "T001",
    "title": "Implement feature X",
    "type": "code_generate",
    "priority": 1,
    "model": "local"
}
```

### task_complete
```python
{
    "task_id": "T001",
    "title": "Implement feature X",
    "type": "code_generate",
    "result": "Implementation complete...",
    "files_written": ["src/feature.py"]
}
```

### task_failed
```python
{
    "task_id": "T001",
    "title": "Implement feature X",
    "type": "code_generate",
    "error": "Error message..."
}
```

### phase_transition
```python
{
    "from_phase": "PLANNING",
    "to_phase": "READY",
    "project_name": "myproject"
}
```

### session_start
```python
{
    "project_name": "myproject",
    "project_dir": "/path/to/project",
    "started_at": "2024-01-01T12:00:00",
    "task_count": 10
}
```

### session_end
```python
{
    "project_name": "myproject",
    "project_dir": "/path/to/project",
    "started_at": "2024-01-01T12:00:00",
    "ended_at": "2024-01-01T14:00:00",
    "duration_seconds": 7200,
    "tasks_done": 8,
    "tasks_total": 10,
    "summary": "Session completed successfully"
}
```

## Best Practices

1. **Use background mode** for notifications to avoid blocking task execution
2. **Keep hooks simple** - complex logic should be in external scripts/services
3. **Handle errors gracefully** - hook errors are logged but don't crash the agent
4. **Set appropriate timeouts** - sync hooks should complete quickly
5. **Use the allowlist** - only allow necessary commands for security

## Example: Slack Integration

```yaml
hooks:
  enabled: true
  tasks:
    - name: slack_task_start
      event: task_start
      type: http
      url: "${SLACK_WEBHOOK_URL}"
      method: POST
      payload_template: |
        {
          "text": "🚀 Starting task {{task_id}}",
          "attachments": [{
            "color": "good",
            "fields": [
              {"title": "Task", "value": "{{title}}", "short": true},
              {"title": "Type", "value": "{{type}}", "short": true}
            ]
          }]
        }
      mode: background
      timeout: 5

    - name: slack_task_complete
      event: task_complete
      type: http
      url: "${SLACK_WEBHOOK_URL}"
      method: POST
      payload_template: |
        {
          "text": "✅ Task {{task_id}} completed",
          "attachments": [{
            "color": "good",
            "fields": [
              {"title": "Task", "value": "{{title}}", "short": true},
              {"title": "Files", "value": "{{files_written | length}}", "short": true}
            ]
          }]
        }
      mode: background
      timeout: 5

    - name: slack_task_failed
      event: task_failed
      type: http
      url: "${SLACK_WEBHOOK_URL}"
      method: POST
      payload_template: |
        {
          "text": "❌ Task {{task_id}} failed",
          "attachments": [{
            "color": "danger",
            "fields": [
              {"title": "Task", "value": "{{title}}", "short": true},
              {"title": "Error", "value": "{{error | truncate(100)}}", "short": false}
            ]
          }]
        }
      mode: background
      timeout: 5
```

## Troubleshooting

### Hooks not firing
- Check `hooks.enabled: true` in `.orchid.yaml`
- Verify event type matches exactly
- Check logs for hook registration errors

### Shell commands blocked
- Add command to `shell_allowlist`
- Use full command path if needed

### HTTP timeouts
- Increase `timeout` value
- Check network connectivity
- Verify webhook URL is accessible