"""Hooks CLI subcommands for Orchid V2.

Provides CLI interface for managing hook configurations:
  orchid hooks list          - List all configured hooks
  orchid hooks show NAME     - Show details of a specific hook
  orchid hooks validate      - Validate hook configuration
  orchid hooks test EVENT    - Test hooks for a specific event
  orchid hooks stats         - Show hook statistics
  orchid hooks add           - Add a new hook
  orchid hooks remove        - Remove a hook

Usage in .orchid.yaml:
    hooks:
      enabled: true
      tasks:
        - name: notify_task_start
          event: task_start
          type: shell
          command: echo "Task {{task_id}} started"
          mode: background
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()


def register_hooks_cli(parent_app: typer.Typer) -> None:
    """Register hooks CLI subcommands with the parent app."""
    
    # Create a sub-app for hooks
    hooks_app = typer.Typer(help="Manage hook configurations")
    parent_app.add_typer(hooks_app, name="hooks", help="Manage hook configurations")

    @hooks_app.command()
    def list(
        project: str = typer.Option(".", "--project", "-p", help="Project directory"),
        section: str | None = typer.Option(None, "--section", "-s", help="Filter by section: tasks, phases, agent, session"),
        event: str | None = typer.Option(None, "--event", "-e", help="Filter by event type"),
        hook_type: str | None = typer.Option(None, "--type", "-t", help="Filter by hook type: shell, http, python"),
    ) -> None:
        """List all configured hooks for a project."""
        from orchid import config as cfg
        from orchid.hooks.schema import VALID_HOOK_TYPES
        
        proj_path = Path(project).expanduser().resolve()
        
        if not proj_path.exists():
            console.print(f"[red]Project directory not found: {proj_path}[/red]")
            raise typer.Exit(1)
        
        if not (proj_path / ".orchid.yaml").exists():
            console.print(f"[red]Not an Orchid project: {proj_path}[/red]")
            raise typer.Exit(1)
        
        cfg.configure_for_project(proj_path)
        hooks_config = cfg.get("hooks", {})
        
        if not hooks_config.get("enabled", False):
            console.print("[yellow]Hooks are disabled for this project.[/yellow]")
            console.print("[dim]Enable by adding 'hooks.enabled: true' to .orchid.yaml[/dim]")
            return
        
        # Collect all hooks from all sections
        all_hooks = []
        sections = ["tasks", "phases", "agent", "session"]
        
        for sec in sections:
            sec_hooks = hooks_config.get(sec, [])
            for hook in sec_hooks:
                hook_with_section = hook.copy()
                hook_with_section["_section"] = sec
                all_hooks.append(hook_with_section)
        
        # Apply filters
        if section:
            section = section.lower()
            if section not in sections:
                console.print(f"[red]Invalid section: {section}. Valid: {', '.join(sections)}[/red]")
                raise typer.Exit(1)
            all_hooks = [h for h in all_hooks if h.get("_section") == section]
        
        if event:
            all_hooks = [h for h in all_hooks if h.get("event") == event]
        
        if hook_type:
            hook_type = hook_type.lower()
            if hook_type not in VALID_HOOK_TYPES:
                console.print(f"[red]Invalid hook type: {hook_type}. Valid: {', '.join(VALID_HOOK_TYPES)}[/red]")
                raise typer.Exit(1)
            all_hooks = [h for h in all_hooks if h.get("type") == hook_type]
        
        if not all_hooks:
            console.print("[yellow]No hooks found matching criteria.[/yellow]")
            return
        
        # Display hooks in a table
        table = Table(
            title=f"[bold]Hooks for {proj_path.name}[/bold]  ({len(all_hooks)} total)",
            show_lines=True,
        )
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Section", style="dim")
        table.add_column("Event", style="green")
        table.add_column("Type", style="yellow")
        table.add_column("Mode", style="magenta")
        table.add_column("Timeout", justify="right")
        
        for hook in all_hooks:
            name = hook.get("name", "unnamed")
            sec = hook.get("_section", "?")
            event_type = hook.get("event", "?")
            h_type = hook.get("type", "?")
            mode = hook.get("mode", "sync")
            timeout = hook.get("timeout", 30)
            
            # Truncate long names
            if len(name) > 20:
                name = name[:17] + "..."
            
            table.add_row(
                name,
                sec,
                event_type,
                h_type,
                mode,
                str(timeout),
            )
        
        console.print(table)
        
        # Summary
        console.print(f"\n[dim]Sections: tasks={hooks_config.get('tasks', []) and len(hooks_config.get('tasks', [])) or 0}, "
                     f"phases={hooks_config.get('phases', []) and len(hooks_config.get('phases', [])) or 0}, "
                     f"agent={hooks_config.get('agent', []) and len(hooks_config.get('agent', [])) or 0}, "
                     f"session={hooks_config.get('session', []) and len(hooks_config.get('session', [])) or 0}[/dim]")

    @hooks_app.command()
    def show(
        name: str = typer.Argument(..., help="Name of the hook to show"),
        project: str = typer.Option(".", "--project", "-p", help="Project directory"),
    ) -> None:
        """Show detailed information about a specific hook."""
        from orchid import config as cfg
        
        proj_path = Path(project).expanduser().resolve()
        
        if not proj_path.exists():
            console.print(f"[red]Project directory not found: {proj_path}[/red]")
            raise typer.Exit(1)
        
        if not (proj_path / ".orchid.yaml").exists():
            console.print(f"[red]Not an Orchid project: {proj_path}[/red]")
            raise typer.Exit(1)
        
        cfg.configure_for_project(proj_path)
        hooks_config = cfg.get("hooks", {})
        
        # Search for the hook
        found_hook = None
        found_section = None
        
        for section in ["tasks", "phases", "agent", "session"]:
            for hook in hooks_config.get(section, []):
                if hook.get("name") == name:
                    found_hook = hook
                    found_section = section
                    break
            if found_hook:
                break
        
        if not found_hook:
            console.print(f"[red]Hook '{name}' not found.[/red]")
            console.print("[dim]Run 'orchid hooks list' to see available hooks.[/dim]")
            raise typer.Exit(1)
        
        # Display hook details
        title = f"Hook: [bold cyan]{name}[/bold cyan]"
        if found_section:
            title += f"  [dim]({found_section})[/dim]"
        
        details = [
            f"[bold]Section:[/bold] {found_section or 'unknown'}",
            f"[bold]Event:[/bold] {found_hook.get('event', '?')}",
            f"[bold]Type:[/bold] {found_hook.get('type', '?')}",
            f"[bold]Mode:[/bold] {found_hook.get('mode', 'sync')}",
            f"[bold]Timeout:[/bold] {found_hook.get('timeout', 30)}s",
        ]
        
        # Type-specific fields
        hook_type = found_hook.get("type")
        if hook_type == "shell":
            details.append(f"[bold]Command:[/bold] {found_hook.get('command', '?')}")
            details.append(f"[bold]Allowlist Check:[/bold] {found_hook.get('allowlist_check', True)}")
        elif hook_type == "http":
            details.append(f"[bold]URL:[/bold] {found_hook.get('url', '?')}")
            details.append(f"[bold]Method:[/bold] {found_hook.get('method', 'POST')}")
            if found_hook.get("headers"):
                details.append(f"[bold]Headers:[/bold] {json.dumps(found_hook.get('headers'))}")
        elif hook_type == "python":
            details.append(f"[bold]Module:[/bold] {found_hook.get('module', '?')}")
            details.append(f"[bold]Function:[/bold] {found_hook.get('function', '?')}")
        
        console.print(Panel(
            "\n".join(details),
            title=title,
            border_style="cyan",
        ))
        
        # Show full configuration as YAML
        console.print("\n[bold]Full Configuration:[/bold]")
        yaml_content = yaml_dump_hook(found_hook, found_section)
        console.print(Syntax(yaml_content, "yaml", theme="monokai", line_numbers=True))

    @hooks_app.command()
    def validate(
        project: str = typer.Option(".", "--project", "-p", help="Project directory"),
        strict: bool = typer.Option(False, "--strict", "-s", help="Fail on warnings"),
    ) -> None:
        """Validate hook configuration."""
        from orchid import config as cfg
        from orchid.hooks.schema import VALID_EVENT_TYPES, VALID_HOOK_TYPES
        
        proj_path = Path(project).expanduser().resolve()
        
        if not proj_path.exists():
            console.print(f"[red]Project directory not found: {proj_path}[/red]")
            raise typer.Exit(1)
        
        if not (proj_path / ".orchid.yaml").exists():
            console.print(f"[red]Not an Orchid project: {proj_path}[/red]")
            raise typer.Exit(1)
        
        cfg.configure_for_project(proj_path)
        hooks_config = cfg.get("hooks", {})
        
        errors = []
        warnings = []
        
        # Check if hooks are enabled
        if not hooks_config.get("enabled", False):
            warnings.append("Hooks are disabled (hooks.enabled: false)")
        
        # Validate each section
        sections = ["tasks", "phases", "agent", "session"]
        hook_names = set()
        
        for section in sections:
            section_hooks = hooks_config.get(section, [])
            
            if type(section_hooks).__name__ != "list":
                errors.append(f"Section '{section}' must be a list")
                continue
            
            for i, hook in enumerate(section_hooks):
                if type(hook).__name__ != "dict":
                    errors.append(f"Hook at index {i} in '{section}' must be a dictionary")
                    continue
                
                # Check required fields
                if "name" not in hook:
                    errors.append(f"Hook at index {i} in '{section}' missing 'name' field")
                    continue
                
                if "event" not in hook:
                    errors.append(f"Hook '{hook['name']}' in '{section}' missing 'event' field")
                    continue
                
                if "type" not in hook:
                    errors.append(f"Hook '{hook['name']}' in '{section}' missing 'type' field")
                    continue
                
                # Check for duplicate names
                name = hook["name"]
                if name in hook_names:
                    warnings.append(f"Duplicate hook name '{name}'")
                hook_names.add(name)
                
                # Validate event type
                event = hook.get("event")
                if event and event not in VALID_EVENT_TYPES:
                    errors.append(f"Hook '{name}': unknown event type '{event}'")
                
                # Validate hook type
                h_type = hook.get("type")
                if h_type and h_type not in VALID_HOOK_TYPES:
                    errors.append(f"Hook '{name}': unknown hook type '{h_type}'")
                
                # Validate mode
                mode = hook.get("mode", "sync")
                if mode not in ["sync", "async", "background"]:
                    errors.append(f"Hook '{name}': invalid mode '{mode}'")
                
                # Type-specific validation
                if h_type == "shell":
                    if "command" not in hook:
                        errors.append(f"Shell hook '{name}': missing 'command' field")
                    elif not hook["command"].strip():
                        errors.append(f"Shell hook '{name}': command cannot be empty")
                
                elif h_type == "http":
                    if "url" not in hook:
                        errors.append(f"HTTP hook '{name}': missing 'url' field")
                    elif not hook["url"].strip():
                        errors.append(f"HTTP hook '{name}': URL cannot be empty")
                
                elif h_type == "python":
                    if "module" not in hook:
                        errors.append(f"Python hook '{name}': missing 'module' field")
                    if "function" not in hook:
                        errors.append(f"Python hook '{name}': missing 'function' field")
        
        # Display results
        if errors or warnings:
            if errors:
                console.print(Panel(
                    "\n".join(f"[red]✗[/red] {e}" for e in errors),
                    title="[bold red]Errors[/bold red]",
                    border_style="red",
                ))
            
            if warnings:
                console.print(Panel(
                    "\n".join(f"[yellow]⚠[/yellow] {w}" for w in warnings),
                    title="[bold yellow]Warnings[/bold yellow]",
                    border_style="yellow",
                ))
            
            if strict and errors:
                raise typer.Exit(1)
        else:
            console.print("[green]✓[/green] Hook configuration is valid")
        
        # Summary
        console.print(f"\n[dim]Total hooks: {len(hook_names)}  Errors: {len(errors)}  Warnings: {len(warnings)}[/dim]")

    @hooks_app.command()
    def test(
        event: str = typer.Argument(..., help="Event type to test (e.g., task_start, phase_transition)"),
        project: str = typer.Option(".", "--project", "-p", help="Project directory"),
        dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would happen without executing"),
    ) -> None:
        """Test hooks for a specific event type."""
        from orchid import config as cfg
        from orchid.hooks.events import HookEvent
        from orchid.hooks.loader import HookLoader
        from orchid.hooks.schema import VALID_EVENT_TYPES
        
        proj_path = Path(project).expanduser().resolve()
        
        if not proj_path.exists():
            console.print(f"[red]Project directory not found: {proj_path}[/red]")
            raise typer.Exit(1)
        
        if not (proj_path / ".orchid.yaml").exists():
            console.print(f"[red]Not an Orchid project: {proj_path}[/red]")
            raise typer.Exit(1)
        
        # Validate event type
        if event not in VALID_EVENT_TYPES:
            console.print(f"[red]Unknown event type: {event}[/red]")
            console.print(f"[dim]Valid types: {', '.join(VALID_EVENT_TYPES)}[/dim]")
            raise typer.Exit(1)
        
        cfg.configure_for_project(proj_path)
        
        # Create test event
        test_event = HookEvent(
            event_type=event,
            data={
                "task_id": "T999",
                "title": "Test Task",
                "timestamp": "2024-01-01T00:00:00Z",
            },
            context={"test": True},
        )
        
        # Load hooks
        loader = HookLoader(proj_path)
        count = loader.load()
        
        if count == 0:
            console.print("[yellow]No hooks loaded.[/yellow]")
            return
        
        # Get handlers for this event
        registry = loader.registry
        handlers = registry.get_handlers_for_event(event)
        
        if not handlers:
            console.print(f"[yellow]No hooks registered for event: {event}[/yellow]")
            return
        
        console.print(Panel(
            f"[bold]Testing hooks for event:[/bold] {event}\n"
            f"[dim]{len(handlers)} handler(s) registered[/dim]",
            border_style="cyan",
        ))
        
        if dry_run:
            for i, handler in enumerate(handlers, 1):
                console.print(f"\n[dim]Handler {i}:[/dim]")
                console.print(f"  Event: {handler.event_type}")
                console.print(f"  Mode: {handler.mode}")
                console.print(f"  Priority: {handler.priority}")
            console.print("\n[dim]Dry run: No hooks were executed.[/dim]")
            return
        
        # Execute handlers
        console.print("\n[bold]Executing handlers:[/bold]")
        
        for i, handler in enumerate(handlers, 1):
            console.print(f"\n[dim]Handler {i} ({handler.mode}):[/dim]")
            
            try:
                result = handler.handler(test_event)
                if result:
                    result_str = str(result)[:200]
                    console.print(f"  [green]Result:[/green] {result_str}")
            except Exception as e:
                console.print(f"  [red]Error:[/red] {e}")
        
        console.print("\n[dim]Test complete.[/dim]")

    @hooks_app.command()
    def stats(
        project: str = typer.Option(".", "--project", "-p", help="Project directory"),
    ) -> None:
        """Show hook statistics."""
        from orchid import config as cfg
        from orchid.hooks.loader import HookLoader
        
        proj_path = Path(project).expanduser().resolve()
        
        if not proj_path.exists():
            console.print(f"[red]Project directory not found: {proj_path}[/red]")
            raise typer.Exit(1)
        
        if not (proj_path / ".orchid.yaml").exists():
            console.print(f"[red]Not an Orchid project: {proj_path}[/red]")
            raise typer.Exit(1)
        
        cfg.configure_for_project(proj_path)
        hooks_config = cfg.get("hooks", {})
        
        # Load hooks to get registry
        loader = HookLoader(proj_path)
        count = loader.load()
        
        # Section counts
        section_counts = {
            "tasks": len(hooks_config.get("tasks", [])),
            "phases": len(hooks_config.get("phases", [])),
            "agent": len(hooks_config.get("agent", [])),
            "session": len(hooks_config.get("session", [])),
        }
        
        # Type counts
        type_counts = {"shell": 0, "http": 0, "python": 0}
        mode_counts = {"sync": 0, "async": 0, "background": 0}
        event_types = set()
        
        for section in ["tasks", "phases", "agent", "session"]:
            for hook in hooks_config.get(section, []):
                h_type = hook.get("type", "")
                if h_type in type_counts:
                    type_counts[h_type] += 1
                
                mode = hook.get("mode", "sync")
                if mode in mode_counts:
                    mode_counts[mode] += 1
                
                event = hook.get("event", "")
                if event:
                    event_types.add(event)
        
        # Display statistics
        table = Table(title="[bold]Hook Statistics[/bold]")
        table.add_column("Category", style="cyan")
        table.add_column("Count", justify="right", style="green")
        
        table.add_row("Total Hooks", str(count))
        table.add_row("Enabled", "Yes" if hooks_config.get("enabled", False) else "No")
        table.add_row("")
        table.add_row("[bold]By Section:[/bold]", "")
        table.add_row("  tasks", str(section_counts["tasks"]))
        table.add_row("  phases", str(section_counts["phases"]))
        table.add_row("  agent", str(section_counts["agent"]))
        table.add_row("  session", str(section_counts["session"]))
        table.add_row("")
        table.add_row("[bold]By Type:[/bold]", "")
        table.add_row("  shell", str(type_counts["shell"]))
        table.add_row("  http", str(type_counts["http"]))
        table.add_row("  python", str(type_counts["python"]))
        table.add_row("")
        table.add_row("[bold]By Mode:[/bold]", "")
        table.add_row("  sync", str(mode_counts["sync"]))
        table.add_row("  async", str(mode_counts["async"]))
        table.add_row("  background", str(mode_counts["background"]))
        table.add_row("")
        table.add_row("Unique Event Types", str(len(event_types)))
        
        console.print(table)
        
        # Event types list
        if event_types:
            console.print("\n[dim]Event types:[/dim]")
            for et in sorted(event_types):
                console.print(f"  • {et}")

    @hooks_app.command()
    def add(
        name: str = typer.Argument(..., help="Name for the new hook"),
        event: str = typer.Argument(..., help="Event type to listen for"),
        hook_type: str = typer.Argument(..., help="Hook type: shell, http, python"),
        project: str = typer.Option(".", "--project", "-p", help="Project directory"),
        section: str = typer.Option("tasks", "--section", "-s", help="Section to add to: tasks, phases, agent, session"),
        command: str | None = typer.Option(None, "--command", "-c", help="Command (for shell hooks)"),
        url: str | None = typer.Option(None, "--url", "-u", help="URL (for HTTP hooks)"),
        method: str = typer.Option("POST", "--method", "-m", help="HTTP method"),
        module: str | None = typer.Option(None, "--module", help="Python module path"),
        function: str | None = typer.Option(None, "--function", "-f", help="Python function name"),
        mode: str = typer.Option("sync", "--mode", help="Execution mode: sync, async, background"),
        timeout: int = typer.Option(30, "--timeout", "-t", help="Timeout in seconds"),
    ) -> None:
        """Add a new hook to the project configuration."""
        import yaml

        from orchid.hooks.schema import VALID_EVENT_TYPES, VALID_EXECUTION_MODES, VALID_HOOK_TYPES
        
        proj_path = Path(project).expanduser().resolve()
        config_path = proj_path / ".orchid.yaml"
        
        if not proj_path.exists():
            console.print(f"[red]Project directory not found: {proj_path}[/red]")
            raise typer.Exit(1)
        
        if not config_path.exists():
            console.print(f"[red]Not an Orchid project: {proj_path}[/red]")
            raise typer.Exit(1)
        
        # Validate inputs
        if event not in VALID_EVENT_TYPES:
            console.print(f"[red]Unknown event type: {event}[/red]")
            console.print(f"[dim]Valid types: {', '.join(VALID_EVENT_TYPES)}[/dim]")
            raise typer.Exit(1)
        
        if hook_type not in VALID_HOOK_TYPES:
            console.print(f"[red]Unknown hook type: {hook_type}[/red]")
            console.print(f"[dim]Valid types: {', '.join(VALID_HOOK_TYPES)}[/dim]")
            raise typer.Exit(1)
        
        if mode not in VALID_EXECUTION_MODES:
            console.print(f"[red]Unknown mode: {mode}[/red]")
            console.print(f"[dim]Valid modes: {', '.join(VALID_EXECUTION_MODES)}[/dim]")
            raise typer.Exit(1)
        
        if section not in ["tasks", "phases", "agent", "session"]:
            console.print(f"[red]Unknown section: {section}[/red]")
            console.print("[dim]Valid sections: tasks, phases, agent, session[/dim]")
            raise typer.Exit(1)
        
        # Validate type-specific requirements
        if hook_type == "shell" and not command:
            console.print("[red]Shell hooks require --command[/red]")
            raise typer.Exit(1)
        
        if hook_type == "http" and not url:
            console.print("[red]HTTP hooks require --url[/red]")
            raise typer.Exit(1)
        
        if hook_type == "python" and (not module or not function):
            console.print("[red]Python hooks require --module and --function[/red]")
            raise typer.Exit(1)
        
        # Load existing config
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        
        # Ensure hooks section exists
        if "hooks" not in config:
            config["hooks"] = {}
        
        hooks = config["hooks"]
        hooks["enabled"] = True
        
        # Create hook
        hook: dict = {
            "name": name,
            "event": event,
            "type": hook_type,
            "mode": mode,
            "timeout": timeout,
        }
        
        if hook_type == "shell":
            hook["command"] = command
            hook["allowlist_check"] = True
        elif hook_type == "http":
            hook["url"] = url
            hook["method"] = method
        elif hook_type == "python":
            hook["module"] = module
            hook["function"] = function
        
        # Add to section
        if section not in hooks:
            hooks[section] = []
        
        # Check for duplicate name
        for existing in hooks[section]:
            if existing.get("name") == name:
                console.print(f"[red]Hook '{name}' already exists in '{section}'[/red]")
                raise typer.Exit(1)
        
        hooks[section].append(hook)
        
        # Write config
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        console.print(f"[green]✓[/green] Added hook '{name}' to '{section}' section")
        console.print(f"[dim]Event: {event}, Type: {hook_type}, Mode: {mode}[/dim]")

    @hooks_app.command()
    def remove(
        name: str = typer.Argument(..., help="Name of the hook to remove"),
        project: str = typer.Option(".", "--project", "-p", help="Project directory"),
        section: str | None = typer.Option(None, "--section", "-s", help="Section to remove from (optional)"),
    ) -> None:
        """Remove a hook from the project configuration."""
        import yaml
        
        proj_path = Path(project).expanduser().resolve()
        config_path = proj_path / ".orchid.yaml"
        
        if not proj_path.exists():
            console.print(f"[red]Project directory not found: {proj_path}[/red]")
            raise typer.Exit(1)
        
        if not config_path.exists():
            console.print(f"[red]Not an Orchid project: {proj_path}[/red]")
            raise typer.Exit(1)
        
        # Load config
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        
        if "hooks" not in config:
            console.print("[yellow]No hooks configured[/yellow]")
            raise typer.Exit(1)
        
        hooks = config["hooks"]
        sections = [section] if section else ["tasks", "phases", "agent", "session"]
        
        found = False
        for sec in sections:
            if sec in hooks:
                original_len = len(hooks[sec])
                hooks[sec] = [h for h in hooks[sec] if h.get("name") != name]
                if len(hooks[sec]) < original_len:
                    found = True
                    console.print(f"[green]✓[/green] Removed hook '{name}' from '{sec}' section")
        
        if not found:
            console.print(f"[yellow]Hook '{name}' not found[/yellow]")
            raise typer.Exit(1)
        
        # Write config
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        console.print("[dim]Configuration updated.[/dim]")


def yaml_dump_hook(hook: dict, section: str | None = None) -> str:
    """Dump a single hook as YAML."""
    import yaml
    
    # Create a minimal hooks config with just this hook
    config = {
        "hooks": {
            "enabled": True,
            section or "tasks": [hook] if section else [hook],
        }
    }
    
    return yaml.dump(config, default_flow_style=False, sort_keys=False)