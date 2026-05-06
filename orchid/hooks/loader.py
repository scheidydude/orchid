"""Hook loader for Orchid V2.

Loads hook configurations from .orchid.yaml and registers them with the registry.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from orchid.hooks.events import HookEvent
from orchid.hooks.registry import HookRegistry
from orchid.hooks.types import HookCategory, HookExecutionMode, HTTPHook, PythonHook, ShellHook

logger = logging.getLogger(__name__)


class HookLoadError(Exception):
    """Raised when hooks.py cannot be imported or a hook is invalid."""


class HookLoader:
    """Loads and registers hooks from configuration.

    Hook configuration in .orchid.yaml:

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

            - name: custom_handler
              event: phase_transition
              type: python
              module: myproject.hooks
              function: on_phase_change
              mode: sync
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.registry = HookRegistry()
        self._loaded_hooks: list[dict] = []
        self._section_counts: dict[str, int] = {}

    def load(self) -> int:
        """Load hooks from .orchid.yaml configuration.

        Returns:
            Number of hooks loaded
        """
        from orchid import config as cfg

        # Configure for this project
        cfg.configure_for_project(self.project_dir)

        hooks_config = cfg.get("hooks", {})

        if not hooks_config.get("enabled", False):
            logger.info("Hooks disabled in configuration")
            return 0

        # ── Wire circuit-breaker configuration ────────────────────────────
        self._configure_circuit_breaker(hooks_config)

        # ── Wire audit logger ─────────────────────────────────────────────
        self._configure_audit_logger()

        count = 0
        self._section_counts = {}

        # Load task lifecycle hooks
        self._section_counts["tasks"] = self._load_section(hooks_config.get("tasks", []), "task")
        count += self._section_counts["tasks"]

        # Load phase transition hooks
        self._section_counts["phases"] = self._load_section(hooks_config.get("phases", []), "phase")
        count += self._section_counts["phases"]

        # Load agent loop hooks
        self._section_counts["agent"] = self._load_section(hooks_config.get("agent", []), "agent")
        count += self._section_counts["agent"]

        # Load session hooks
        self._section_counts["session"] = self._load_section(hooks_config.get("session", []), "session")
        count += self._section_counts["session"]

        # Load @orchid_hook-decorated functions from hooks.py in project root
        count += self._load_project_hooks_py()

        logger.info("Loaded %d hook(s)", count)
        return count

    def _configure_circuit_breaker(self, hooks_config: dict) -> None:
        """Read circuit_breaker config from .orchid.yaml and wire it into the
        CircuitBreakerRegistry singleton."""
        cb_cfg = hooks_config.get("circuit_breaker", {})
        if not cb_cfg:
            return

        from orchid.hooks.circuit_breaker import (
            CircuitBreakerConfig,
            configure_circuit_breaker,
        )

        config = CircuitBreakerConfig(
            enabled=cb_cfg.get("enabled", True),
            failure_threshold=cb_cfg.get("failure_threshold", 5),
            recovery_timeout=cb_cfg.get("recovery_timeout", 60),
            half_open_max_calls=cb_cfg.get("half_open_max_calls", 1),
            success_threshold=cb_cfg.get("success_threshold", 1),
            monitored_events=cb_cfg.get("monitored_events", [
                "task_complete",
                "task_failed",
                "phase_transition",
            ]),
        )
        configure_circuit_breaker(config)
        logger.info(
            "Circuit breaker configured: enabled=%s, threshold=%s, recovery=%ss",
            config.enabled, config.failure_threshold, config.recovery_timeout,
        )

    def _configure_audit_logger(self) -> None:
        """Read audit config from .orchid.yaml and wire it into the
        AuditLogger singleton."""
        from orchid import config as cfg

        hooks_config = cfg.get("hooks", {})
        audit_cfg = hooks_config.get("audit", {})

        if not audit_cfg:
            # Default: enable audit logging
            audit_cfg = {"enabled": True}

        if not audit_cfg.get("enabled", True):
            logger.info("Audit logging disabled in configuration")
            return

        from orchid.hooks.audit import configure_audit_logger

        configure_audit_logger(self.project_dir)
        logger.info(
            "Audit logger configured for %s", self.project_dir,
        )

    def _load_project_hooks_py(self) -> int:
        """Load @orchid_hook-decorated functions from hooks.py in project root."""
        hooks_py = self.project_dir / "hooks.py"
        if not hooks_py.exists():
            return 0

        import importlib.util
        try:
            spec = importlib.util.spec_from_file_location("project_hooks", hooks_py)
            if spec is None or spec.loader is None:
                raise HookLoadError(f"Cannot create module spec for {hooks_py}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except HookLoadError:
            raise
        except Exception as e:
            raise HookLoadError(f"Failed to import hooks.py: {e}") from e

        count = 0
        for name in dir(mod):
            fn = getattr(mod, name)
            if callable(fn) and hasattr(fn, "_orchid_hook_event"):
                event_type = fn._orchid_hook_event
                self.registry.register(event_type, fn)
                self._loaded_hooks.append({
                    "name": name,
                    "event": event_type,
                    "type": "python",
                    "source": "hooks.py",
                })
                logger.debug("Registered hooks.py function %s for event %s", name, event_type)
                count += 1

        if count:
            logger.info("Loaded %d hook(s) from hooks.py", count)
        return count

    def _load_section(self, hooks: list[dict], category: str) -> int:
        """Load hooks from a configuration section."""
        count = 0

        for hook_config in hooks:
            try:
                hook = self._parse_hook(hook_config, category)
                if hook:
                    self._register_hook(hook)
                    self._loaded_hooks.append(hook_config)
                    count += 1
            except Exception as e:
                logger.error("Failed to load hook %s: %s", hook_config.get("name", "?"), e)

        return count

    def _parse_hook(self, config: dict, category: str) -> ShellHook | HTTPHook | PythonHook | None:
        """Parse a hook configuration into a hook object."""
        name = config.get("name", "unnamed")
        event_type = config.get("event", "")
        hook_type = config.get("type", "shell")
        mode_str = config.get("mode", "sync")

        # Validate event type
        if not event_type:
            logger.warning("Hook %s has no event type, skipping", name)
            return None

        # Parse mode
        mode_map = {
            "sync": HookExecutionMode.SYNC,
            "async": HookExecutionMode.ASYNC,
            "background": HookExecutionMode.BACKGROUND,
        }
        mode = mode_map.get(mode_str, HookExecutionMode.SYNC)

        # Map category to HookCategory
        category_map = {
            "task": HookCategory.TASK,
            "phase": HookCategory.PHASE,
            "agent": HookCategory.AGENT,
            "session": HookCategory.SESSION,
        }
        hook_category = category_map.get(category, HookCategory.TASK)

        if hook_type == "shell":
            return self._parse_shell_hook(name, event_type, config, mode, hook_category)
        elif hook_type == "http":
            return self._parse_http_hook(name, event_type, config, mode, hook_category)
        elif hook_type == "python":
            return self._parse_python_hook(name, event_type, config, mode, hook_category)
        else:
            logger.warning("Unknown hook type %s for hook %s", hook_type, name)
            return None

    def _parse_shell_hook(
        self, name: str, event_type: str, config: dict,
        mode: HookExecutionMode, category: HookCategory
    ) -> ShellHook:
        """Parse a shell hook configuration."""
        command = config.get("command", "")
        timeout = config.get("timeout", 60)
        allowlist_check = config.get("allowlist_check", True)

        return ShellHook(
            name=name,
            event_type=event_type,
            command=command,
            category=category,
            mode=mode,
            timeout=timeout,
            allowlist_check=allowlist_check,
        )

    def _parse_http_hook(
        self, name: str, event_type: str, config: dict,
        mode: HookExecutionMode, category: HookCategory
    ) -> HTTPHook:
        """Parse an HTTP hook configuration."""
        url = config.get("url", "")
        method = config.get("method", "POST")
        headers = config.get("headers", {})
        payload_template = config.get("payload_template", "")
        timeout = config.get("timeout", 10)

        return HTTPHook(
            name=name,
            event_type=event_type,
            url=url,
            method=method,
            headers=headers,
            payload_template=payload_template,
            category=category,
            mode=mode,
            timeout=timeout,
        )

    def _parse_python_hook(
        self, name: str, event_type: str, config: dict,
        mode: HookExecutionMode, category: HookCategory
    ) -> PythonHook | None:
        """Parse a Python hook configuration."""
        module = config.get("module", "")
        function = config.get("function", "")

        if not module or not function:
            logger.warning(
                "Python hook %s missing module or function, skipping", name
            )
            return None

        # Import the module and get the function
        try:
            import importlib
            mod = importlib.import_module(module)
            callback = getattr(mod, function)
            if not callable(callback):
                logger.warning(
                    "Python hook %s: %s.%s is not callable", name, module, function
                )
                return None
        except (ImportError, AttributeError) as e:
            logger.error(
                "Failed to import Python hook %s: %s", name, e
            )
            return None

        return PythonHook(
            name=name,
            event_type=event_type,
            callback=callback,
            category=category,
            mode=mode,
        )

    def _register_hook(self, hook: ShellHook | HTTPHook | PythonHook) -> None:
        """Register a hook with the registry."""
        # Convert mode to string
        mode_map = {
            HookExecutionMode.SYNC: "sync",
            HookExecutionMode.ASYNC: "async",
            HookExecutionMode.BACKGROUND: "background",
        }
        mode_str = mode_map.get(hook.mode, "sync")

        # Create handler based on hook type
        if isinstance(hook, ShellHook):
            handler = self._create_shell_handler(hook)
        elif isinstance(hook, HTTPHook):
            handler = self._create_http_handler(hook)
        elif isinstance(hook, PythonHook):
            handler = hook.callback
        else:
            logger.warning("Unknown hook type: %s", type(hook))
            return

        # Register with priority based on mode (sync hooks get higher priority)
        priority_map = {
            HookExecutionMode.SYNC: 100,
            HookExecutionMode.ASYNC: 50,
            HookExecutionMode.BACKGROUND: 10,
        }
        priority = priority_map.get(hook.mode, 50)

        self.registry.register(
            event_type=hook.event_type,
            handler=handler,
            priority=priority,
            mode=mode_str,
            timeout=hook.timeout,
        )

        logger.debug("Registered hook %s for event %s", hook.name, hook.event_type)

    def _create_shell_handler(self, hook: ShellHook) -> callable:
        """Create a handler for a shell hook.

        Context JSON is passed on stdin. If stdout parses as JSON with
        {"block": true}, the handler returns a blocking signal to HookRegistry.

        Audit logging is wired in: every invocation is recorded to the
        project's .orchid/audit_log.jsonl with status, duration, and error.
        """
        from orchid.hooks.audit import get_audit_logger

        event_type = hook.event_type

        def handler(event: HookEvent) -> str | dict:
            start = time.monotonic()
            task_id = event.context.get("task_id", "")

            # ── Audit: allowlist gate ───────────────────────────────────
            audit_logger = get_audit_logger()

            if hook.allowlist_check and not self._is_command_allowed(hook.command):
                duration = time.monotonic() - start
                if audit_logger:
                    audit_logger.log_hook(
                        event_type=event_type,
                        hook_name=hook.name,
                        hook_type="shell",
                        status="blocked",
                        duration_s=duration,
                        task_id=task_id,
                        command=hook.command,
                    )
                logger.warning(
                    "Shell hook %s blocked: command not in allowlist", hook.name
                )
                return "[blocked]"

            command = self._substitute_vars(hook.command, event)

            try:
                context_json = json.dumps({
                    "event_type": event.event_type,
                    "data": event.data,
                    "context": event.context,
                    "timestamp": event.timestamp,
                })

                import subprocess
                result = subprocess.run(
                    command,
                    shell=True,
                    input=context_json,
                    capture_output=True,
                    text=True,
                    timeout=hook.timeout,
                    cwd=str(self.project_dir),
                )
                duration = time.monotonic() - start

                if result.returncode != 0:
                    logger.error("Shell hook %s failed: %s", hook.name, result.stderr)
                    if audit_logger:
                        audit_logger.log_hook(
                            event_type=event_type,
                            hook_name=hook.name,
                            hook_type="shell",
                            status="failure",
                            duration_s=duration,
                            error=result.stderr[:500],
                            task_id=task_id,
                            command=hook.command,
                        )

                stdout = result.stdout.strip()
                if stdout:
                    try:
                        response = json.loads(stdout)
                        if response.get("block"):
                            if audit_logger:
                                audit_logger.log_hook(
                                    event_type=event_type,
                                    hook_name=hook.name,
                                    hook_type="shell",
                                    status="success",
                                    duration_s=duration,
                                    task_id=task_id,
                                    command=hook.command,
                                )
                            return {
                                "blocked": True,
                                "error": response.get("reason", "blocked by hook"),
                                "mutated_context": response.get("mutated_context"),
                            }
                        if "mutated_context" in response:
                            if audit_logger:
                                audit_logger.log_hook(
                                    event_type=event_type,
                                    hook_name=hook.name,
                                    hook_type="shell",
                                    status="success",
                                    duration_s=duration,
                                    task_id=task_id,
                                    command=hook.command,
                                )
                            return {"mutated_context": response["mutated_context"]}
                    except json.JSONDecodeError:
                        pass

                if audit_logger:
                    audit_logger.log_hook(
                        event_type=event_type,
                        hook_name=hook.name,
                        hook_type="shell",
                        status="success",
                        duration_s=duration,
                        task_id=task_id,
                        command=hook.command,
                    )
                return stdout or result.stderr or "[executed]"
            except subprocess.TimeoutExpired:
                duration = time.monotonic() - start
                logger.error("Shell hook %s timed out", hook.name)
                if audit_logger:
                    audit_logger.log_hook(
                        event_type=event_type,
                        hook_name=hook.name,
                        hook_type="shell",
                        status="timeout",
                        duration_s=duration,
                        error=f"exceeded {hook.timeout}s",
                        task_id=task_id,
                        command=hook.command,
                    )
                return "[timeout]"
            except Exception as e:
                duration = time.monotonic() - start
                logger.error("Shell hook %s error: %s", hook.name, e)
                if audit_logger:
                    audit_logger.log_hook(
                        event_type=event_type,
                        hook_name=hook.name,
                        hook_type="shell",
                        status="error",
                        duration_s=duration,
                        error=str(e),
                        task_id=task_id,
                        command=hook.command,
                    )
                return f"[error: {e}]"

        return handler

    def _create_http_handler(self, hook: HTTPHook) -> callable:
        """Create a handler for an HTTP hook with circuit-breaker wiring."""
        event_type = hook.event_type

        def handler(event: HookEvent) -> dict:
            """Make HTTP request with event data, guarded by circuit breaker."""

            # ── Circuit-breaker gate ────────────────────────────────────
            from orchid.hooks.circuit_breaker import allow_request, record_failure, record_success

            if not allow_request(event_type):
                logger.warning(
                    "HTTP hook %s for event %s: circuit breaker OPEN — request rejected",
                    hook.name, event_type,
                )
                return {
                    "status_code": 0,
                    "response": "",
                    "circuit_breaker": "open",
                }

            # Substitute event data into URL and payload
            url = self._substitute_vars(hook.url, event)
            payload = hook.payload_template
            if payload:
                payload = self._substitute_vars(payload, event)

            try:
                import requests
                response = requests.request(
                    method=hook.method,
                    url=url,
                    headers=hook.headers,
                    data=payload,
                    timeout=hook.timeout,
                )
                # ── Record outcome ──────────────────────────────────────
                if response.ok:
                    record_success(event_type)
                else:
                    record_failure(event_type)

                return {
                    "status_code": response.status_code,
                    "response": response.text[:500],
                }
            except Exception as e:
                record_failure(event_type)
                logger.error("HTTP hook %s error: %s", hook.name, e)
                return {"error": str(e)}

        return handler

    def _is_command_allowed(self, command: str) -> bool:
        """Check if a shell command is in the allowlist."""
        from orchid import config as cfg

        allowlist = cfg.get("hooks.shell_allowlist", [])

        # Extract the base command (first word)
        base_cmd = command.split()[0] if command else ""

        # Check exact match
        if base_cmd in allowlist:
            return True

        # Check prefix matches
        for allowed in allowlist:
            if command.startswith(allowed):
                return True

        return False

    def _substitute_vars(self, template: str, event: HookEvent) -> str:
        """Substitute event variables into a template string."""
        if not template:
            return template

        result = template

        # Top-level event data
        for key, value in event.data.items():
            if isinstance(value, str):
                result = result.replace(f"{{{{{key}}}}}", value)

        # Context data
        for key, value in event.context.items():
            if isinstance(value, str):
                result = result.replace(f"{{{{context.{key}}}}}", value)

        # Timestamp
        result = result.replace("{{timestamp}}", event.timestamp)

        # Event type
        result = result.replace("{{event_type}}", event.event_type)

        # JSON representation of full event data
        if "{{event_data}}" in result:
            import json
            result = result.replace("{{event_data}}", json.dumps(event.data))

        return result

    def get_loaded_hooks(self) -> list[dict]:
        """Get list of loaded hook configurations."""
        return self._loaded_hooks.copy()

    def _count_section(self, section: str) -> int:
        """Get count of hooks loaded from a section."""
        return self._section_counts.get(section, 0)