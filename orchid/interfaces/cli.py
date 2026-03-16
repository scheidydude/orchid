"""CLI entry point.

Top-level flags handle the common case:
  orchid --project <path> --mode auto
  orchid --project <path> --mode interactive
  orchid --project <path> --status
  orchid --project <path> --add-task "Build the login page"

Subcommands for less frequent operations:
  orchid init [PATH]           scaffold CLAUDE.md / tasks.md / .orchid.yaml
  orchid decide TITLE -d TEXT  record an architectural decision
"""

from __future__ import annotations

import importlib.metadata
import logging
import shutil

from dotenv import load_dotenv

load_dotenv()
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

def _version_callback(value: bool) -> None:
    if value:
        try:
            version = importlib.metadata.version("orchid")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown (package not installed)"
        console.print(f"orchid {version}")
        raise typer.Exit()


app = typer.Typer(
    name="orchid",
    help="Orchid — AI agent orchestration framework",
    invoke_without_command=True,
    no_args_is_help=False,
)
console = Console()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _make_session(project: str) -> "Session":
    from orchid.session import Session
    s = Session(project_dir=project)
    s.load()
    return s


def _resolve_project(project: str) -> Path:
    return Path(project).expanduser().resolve()


# ── Main callback — handles --mode, --status, --add-task ─────────────────────


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None, "--version", "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    project: Optional[List[str]] = typer.Option(
        None, "--project", "-p",
        help="Path to the project directory. Repeat for --multi mode.",
    ),
    multi: bool = typer.Option(False, "--multi", help="Run multiple projects in parallel"),
    mode: Optional[str] = typer.Option(
        None, "--mode", "-m",
        help="Run mode: auto (autonomous) | interactive (chat)",
    ),
    status: bool = typer.Option(False, "--status", "-s", help="Show task board and hot memory"),
    add_task: Optional[str] = typer.Option(
        None, "--add-task", "-a",
        help="Add a new task (title string). Use --type and --priority to customise.",
    ),
    task_type: str = typer.Option("draft", "--type", help="Task type for --add-task"),
    priority: int = typer.Option(2, "--priority", help="Priority for --add-task (1=high)"),
    max_tasks: int = typer.Option(50, "--max-tasks", "-n", help="Max tasks for auto mode"),
    recall: Optional[str] = typer.Option(
        None, "--recall", help="Query vector memory and print top results",
    ),
    search: Optional[str] = typer.Option(
        None, "--search", help="Run a web search and print results (embeds if vector enabled)",
    ),
    code_model: Optional[str] = typer.Option(
        None, "--code-model",
        help="Force model for all tasks: claude | local | auto",
    ),
    tail: bool = typer.Option(False, "--tail", help="Tail the most recent live agent log"),
    inject: Optional[str] = typer.Option(
        None, "--inject", help="Inject context into the running agent via inject.queue",
    ),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
) -> None:
    if ctx.invoked_subcommand:
        return

    _setup_logging(log_level)

    # Resolve project path(s); default to "." when none provided
    resolved_projects = [str(_resolve_project(p)) for p in (project or ["."])]
    proj = resolved_projects[0]

    # Multi-project mode: two or more --project flags with --multi
    if multi:
        if len(resolved_projects) < 2:
            console.print(
                "[red]--multi requires at least two --project flags.[/red]\n"
                "Example: orchid --multi --project ~/a --project ~/b"
            )
            raise typer.Exit(1)
        _cmd_multi(resolved_projects, code_model=code_model)
        return

    if status:
        _cmd_status(proj)
        return

    if recall is not None:
        _cmd_recall(proj, recall)
        return

    if search is not None:
        _cmd_search(proj, search)
        return

    if add_task:
        _cmd_add_task(proj, add_task, task_type, priority)
        return

    if tail:
        _cmd_tail(proj)
        return

    if inject is not None:
        _cmd_inject(proj, inject)
        return

    if mode == "auto":
        _cmd_auto(proj, max_tasks, code_model=code_model)
    elif mode == "interactive":
        _cmd_interactive(proj)
    else:
        # No flags given — show help
        console.print(ctx.get_help())


# ── Mode implementations ──────────────────────────────────────────────────────


def _cmd_auto(project: str, max_tasks: int, code_model: str | None = None) -> None:
    """Autonomous mode — run all pending tasks."""
    session = _make_session(project)
    pending = [t for t in session.tasks if t.status.value == "TODO"]

    model_note = f"  Model override: {code_model}" if code_model else ""
    console.print(Panel(
        f"[bold green]Orchid — Autonomous Mode[/bold green]\n"
        f"Project: [cyan]{session.project_name}[/cyan]"
        + (f"\n{session.project_description}" if session.project_description else "")
        + f"\nTasks pending: {len(pending)}"
        + model_note,
        border_style="green",
    ))

    if not pending:
        console.print("[yellow]No pending tasks. Add tasks to tasks.md and rerun.[/yellow]")
        raise typer.Exit()

    from orchid.orchestrator import Orchestrator
    orch = Orchestrator(session, cli_model_override=code_model)
    try:
        orch.run_loop(max_tasks=max_tasks)
    finally:
        session.close(summary="Autonomous run complete.")
        console.print("[green]Session saved.[/green]")


def _cmd_interactive(project: str, model: str = "claude") -> None:
    """Interactive chat mode."""
    session = _make_session(project)
    from orchid.agents.base import BaseAgent

    class _ChatAgent(BaseAgent):
        model_key = model

    agent = _ChatAgent(session_context=session.context_block())

    console.print(Panel(
        f"[bold cyan]Orchid — Interactive Mode[/bold cyan]\n"
        f"Project: [cyan]{session.project_name}[/cyan]  |  Model: {model}\n"
        "Type [bold]exit[/bold] or [bold]quit[/bold] to end.",
        border_style="cyan",
    ))

    while True:
        try:
            user_input = Prompt.ask("\n[bold blue]You[/bold blue]")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in {"exit", "quit", "q"}:
            break
        result = agent.run(user_input)
        console.print(Panel(Markdown(result), title="[bold green]Orchid[/bold green]", border_style="green"))

    session.close(summary="Interactive session.")
    console.print("[dim]Session saved. Goodbye.[/dim]")


def _count_last_session_delegations(project: str) -> int:
    """Count delegation events in the most recent session log, if any."""
    import json as _json
    log_dir = Path(project) / ".orchid" / "session_logs"
    if not log_dir.exists():
        return 0
    logs = sorted(log_dir.glob("session_*.jsonl"))
    if not logs:
        return 0
    count = 0
    try:
        for line in logs[-1].read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = _json.loads(line)
            if rec.get("type") == "delegation":
                count += 1
    except Exception:
        pass
    return count


def _cmd_status(project: str) -> None:
    """Print task board and hot memory."""
    from rich.markup import escape
    session = _make_session(project)
    proj_path = _resolve_project(project)

    status_color = {
        "TODO": "white", "IN_PROGRESS": "yellow", "DONE": "green",
        "BLOCKED": "red", "CANCELLED": "dim",
    }

    table = Table(
        title=f"[bold]{escape(session.project_name)}[/bold]  {escape(str(proj_path))}",
        show_lines=True,
    )
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", style="bold")
    table.add_column("Title")
    table.add_column("Type", style="dim")
    table.add_column("P", justify="center")
    table.add_column("Deps", style="dim")

    completed_ids = {t.id for t in session.tasks if t.status.value == "DONE"}
    for t in session.tasks:
        color = status_color.get(t.status.value, "white")
        deps_str = ""
        if t.depends_on:
            waiting = [d for d in t.depends_on if d not in completed_ids]
            deps_str = ",".join(t.depends_on)
            if waiting:
                deps_str = f"⏳ {','.join(waiting)}"
        table.add_row(t.id, f"[{color}]{t.status.value}[/{color}]", t.title, t.type, str(t.priority), deps_str)

    console.print(table)

    delegation_count = _count_last_session_delegations(str(proj_path))
    summary_parts = [
        f"tasks={len(session.tasks)}",
        f"decisions={len(session.decisions)}",
        f"delegations={delegation_count}",
    ]
    console.print(f"[dim]{' · '.join(summary_parts)}[/dim]")

    if session.hot_memory:
        console.print(Panel(
            Markdown(session.hot_memory[:2000]),
            title="[bold]Hot Memory (CLAUDE.md)[/bold]",
            border_style="blue",
        ))


def _cmd_search(project: str, query: str) -> None:
    """Run a web search and print results."""
    from orchid import config as cfg  # noqa: PLC0415
    from orchid.tools.search import WebSearchTool, reset_backend_cache  # noqa: PLC0415
    from rich.markup import escape  # noqa: PLC0415

    proj_path = _resolve_project(project)
    cfg.configure_for_project(proj_path)
    reset_backend_cache()

    # Optionally wire vector memory for embedding
    vector_memory = None
    if cfg.get("web_search.embed_results", True) and cfg.get("vector_memory.enabled", True):
        from orchid.memory.vector import VectorMemory  # noqa: PLC0415
        vector_memory = VectorMemory(project_dir=proj_path)

    project_name = proj_path.name
    tool = WebSearchTool(vector_memory=vector_memory, project_name=project_name)

    n = cfg.get("web_search.max_results", 5)
    results = tool.search(query, n=n)

    backend_name = ""
    if results and results[0].get("source"):
        backend_name = results[0]["source"]

    console.print(Panel(
        f"[bold]Search:[/bold] {escape(query)}"
        + (f"  [dim]via {backend_name}[/dim]" if backend_name else ""),
        border_style="cyan",
    ))

    has_error = len(results) == 1 and results[0].get("title") in ("error", "")
    if has_error:
        console.print(f"[red]{escape(results[0]['snippet'])}[/red]")
        return

    for i, r in enumerate(results, 1):
        title = escape(r.get("title", "(no title)"))
        url = escape(r.get("url", ""))
        snippet = escape(r.get("snippet", ""))
        body = f"[bold]{title}[/bold]"
        if url:
            body += f"\n[dim]{url}[/dim]"
        if snippet:
            body += f"\n{snippet[:300]}"
        console.print(Panel(body, title=f"[{i}]", border_style="dim"))

    if vector_memory and vector_memory.available and cfg.get("web_search.embed_results", True):
        console.print(f"[dim]Results embedded into vector store.[/dim]")


def _cmd_recall(project: str, query: str) -> None:
    """Query vector memory and pretty-print results."""
    from orchid.memory.vector import VectorMemory  # noqa: PLC0415
    from orchid import config as cfg  # noqa: PLC0415

    proj_path = _resolve_project(project)
    cfg.configure_for_project(proj_path)

    vm = VectorMemory(project_dir=proj_path)
    if not vm.available:
        console.print("[yellow]Vector memory not available for this project.[/yellow]")
        return

    n = cfg.get("vector_memory.n_results", 5)
    results = vm.query(query, n=n)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    from rich.markup import escape  # noqa: PLC0415

    console.print(Panel(
        f"[bold]Recall:[/bold] {escape(query)}  [dim]({len(results)} results)[/dim]",
        border_style="magenta",
    ))

    for i, r in enumerate(results, 1):
        meta = r["metadata"]
        rtype = meta.get("type", "note")
        ts = meta.get("timestamp", "")[:19].replace("T", " ")
        sid = meta.get("session_id", "")
        score = 1 - r["distance"]
        title_parts = [f"[{i}]", f"type={rtype}"]
        if sid:
            title_parts.append(f"session={sid}")
        if ts:
            title_parts.append(ts)
        title_parts.append(f"score={score:.3f}")
        console.print(
            Panel(
                escape(r["text"][:400]),
                title="  ".join(title_parts),
                border_style="dim",
            )
        )


def _cmd_tail(project: str) -> None:
    """Tail the most recent live agent log."""
    import time
    from rich.markup import escape

    log_dir = _resolve_project(project) / ".orchid" / "session_logs"
    if not log_dir.exists():
        console.print("[yellow]No session logs found.[/yellow]")
        return

    # Prefer active .live.log; fall back to most recent .log
    live_logs = sorted(log_dir.glob("*.live.log"))
    finished_logs = sorted(log_dir.glob("*.log"))
    if live_logs:
        log_path = live_logs[-1]
        console.print(f"[cyan]Tailing live log: {log_path.name}[/cyan]  (Ctrl+C to stop)")
    elif finished_logs:
        log_path = finished_logs[-1]
        console.print(f"[dim]No active log. Showing last completed: {log_path.name}[/dim]")
        console.print(escape(log_path.read_text(encoding="utf-8")))
        return
    else:
        console.print("[yellow]No log files found.[/yellow]")
        return

    try:
        with open(log_path, encoding="utf-8") as f:
            # Print existing content first
            existing = f.read()
            if existing:
                console.print(escape(existing), end="")
            # Then tail new content
            while log_path.exists():
                line = f.readline()
                if line:
                    console.print(escape(line), end="")
                else:
                    time.sleep(0.2)
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


def _cmd_inject(project: str, text: str) -> None:
    """Inject context into a running agent via the inject.queue file."""
    proj_path = _resolve_project(project)
    queue_path = proj_path / ".orchid" / "inject.queue"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with open(queue_path, "a", encoding="utf-8") as f:
        f.write(text + "\n")
    console.print(f"[green]Injected into queue: {text[:100]}[/green]")
    console.print(f"[dim]Agent will pick this up on next ReAct iteration.[/dim]")


def _cmd_add_task(project: str, title: str, task_type: str, priority: int) -> None:
    """Add a task directly from the CLI."""
    session = _make_session(project)
    from orchid.memory.state import Task, save_tasks

    tid = f"T{len(session.tasks) + 1:03d}"
    t = Task(id=tid, title=title, type=task_type, priority=priority)
    session.tasks.append(t)
    save_tasks(session.tasks, project)
    console.print(f"[green]Added {tid}: {title} (type={task_type}, p{priority})[/green]")


def _cmd_multi(projects: list[str], code_model: str | None = None) -> None:
    """Run multiple projects in parallel worker processes."""
    from orchid.multi import MultiOrchid
    from orchid.interfaces.multi_formatter import format_notification as fmt_multi

    console.print(Panel(
        "[bold green]Orchid — Multi-Project Mode[/bold green]\n"
        + "\n".join(f"  • {p}" for p in projects)
        + (f"\n  Model: {code_model}" if code_model else ""),
        border_style="green",
    ))

    def _on_notification(notification: dict) -> None:
        event = notification.get("event", "")
        project = notification.get("project", "")
        data = notification.get("data", {})
        msg = fmt_multi(event, project, data)
        if msg:
            console.print(msg)

    orch = MultiOrchid(
        projects=projects,
        code_model=code_model,
        notification_callback=_on_notification,
    )
    try:
        orch.start()
    except KeyboardInterrupt:
        orch.stop()
        console.print("[dim]Multi-project run stopped.[/dim]")
    console.print("[green]All projects complete.[/green]")


# ── Subcommands ───────────────────────────────────────────────────────────────


@app.command()
def init(
    path: str = typer.Argument(".", help="Directory to initialise (defaults to cwd)"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Project name (defaults to dirname)"),
    description: str = typer.Option("", "--description", "-d", help="One-line project description"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files"),
) -> None:
    """Scaffold CLAUDE.md, tasks.md, and .orchid.yaml in a project directory."""
    target = Path(path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    project_name = name or target.name
    subs = {"project_name": project_name, "description": description}

    created: list[str] = []
    skipped: list[str] = []

    for tmpl in _TEMPLATES_DIR.iterdir():
        dest = target / tmpl.name
        if dest.exists() and not force:
            skipped.append(tmpl.name)
            continue
        content = tmpl.read_text(encoding="utf-8")
        for key, val in subs.items():
            content = content.replace("{" + key + "}", val)
        dest.write_text(content, encoding="utf-8")
        created.append(tmpl.name)

    # Ensure .orchid/ is in .gitignore
    gitignore = target / ".gitignore"
    entry = ".orchid/"
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8")
        if entry not in existing:
            gitignore.write_text(existing.rstrip() + f"\n{entry}\n", encoding="utf-8")
            created.append(".gitignore (updated)")
    else:
        gitignore.write_text(f"{entry}\n", encoding="utf-8")
        created.append(".gitignore")

    for f in created:
        console.print(f"[green]  created[/green]  {f}")
    for f in skipped:
        console.print(f"[dim]  skipped[/dim]  {f} (already exists, use --force to overwrite)")

    console.print(f"\n[bold green]Orchid initialised in {target}[/bold green]")
    console.print(f"  Edit [cyan]CLAUDE.md[/cyan] and [cyan].orchid.yaml[/cyan], then add tasks and run:")
    console.print(f"  [bold]orchid --project {path} --mode auto[/bold]")


@app.command()
def decide(
    title: str = typer.Argument(..., help="Short decision title"),
    decision: str = typer.Option(..., "--decision", "-d", help="The decision made"),
    rationale: str = typer.Option("", "--rationale", "-r", help="Why this decision was made"),
    project: str = typer.Option(".", "--project", "-p", help="Project directory"),
) -> None:
    """Record an architectural decision in the project's decision log."""
    from orchid.memory.decisions import record_decision
    rec = record_decision(title, decision, rationale, project_dir=project)
    console.print(f"[green]Recorded {rec['id']}: {title}[/green]")


@app.command()
def task(
    action: str = typer.Argument(..., help="add | done | block | cancel"),
    project: str = typer.Option(".", "--project", "-p"),
    task_id: Optional[str] = typer.Option(None, "--id"),
    title: str = typer.Argument(..., help="Task title (required for add)"),
    task_type: str = typer.Option("draft", "--type"),
    priority: int = typer.Option(2, "--priority"),
    description: str = typer.Option("", "--desc", "-d"),
) -> None:
    """Manage tasks: add, mark done, block, or cancel."""
    session = _make_session(project)
    from orchid.memory.state import Task, TaskStatus, save_tasks

    if action == "add":
        if not title:
            console.print("[red]--title required for add action[/red]")
            raise typer.Exit(1)
        tid = task_id or f"T{len(session.tasks) + 1:03d}"
        t = Task(id=tid, title=title, type=task_type, priority=priority, description=description)
        session.tasks.append(t)
        save_tasks(session.tasks, project)
        console.print(f"[green]Added {tid}: {title}[/green]")

    elif action in ("done", "block", "cancel"):
        if not task_id:
            console.print("[red]--id required[/red]")
            raise typer.Exit(1)
        status_map = {
            "done": TaskStatus.DONE,
            "block": TaskStatus.BLOCKED,
            "cancel": TaskStatus.CANCELLED,
        }
        new_status = status_map[action]
        if session.update_task_status(task_id, new_status):
            save_tasks(session.tasks, project)
            console.print(f"[green]Marked {task_id} as {new_status.value}[/green]")
        else:
            console.print(f"[red]Task {task_id} not found[/red]")
            raise typer.Exit(1)

    else:
        console.print(f"[red]Unknown action: {action}. Use add|done|block|cancel[/red]")
        raise typer.Exit(1)


@app.command(name="multi")
def multi_cmd(
    action: str = typer.Argument("start", help="start | status | stop"),
    project: Optional[List[str]] = typer.Option(
        None, "--project", "-p",
        help="Project directory (repeat for multiple projects)",
    ),
    code_model: Optional[str] = typer.Option(
        None, "--code-model",
        help="Force model for all tasks: claude | local | auto",
    ),
    workers: int = typer.Option(4, "--workers", "-w", help="Max parallel worker processes"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
) -> None:
    """Run multiple projects in parallel (persistent config subcommand).

    Examples:
      orchid multi start --project ~/a --project ~/b
      orchid multi status
    """
    _setup_logging(log_level)

    if action == "start":
        projects = [str(_resolve_project(p)) for p in (project or [])]
        if not projects:
            # Fall back to projects list from orchid.defaults.yaml / .orchid.yaml
            from orchid import config as cfg
            projects = [str(_resolve_project(p)) for p in cfg.get("multi.projects", [])]
        if not projects:
            console.print(
                "[red]No projects specified.[/red]\n"
                "Use --project or add paths under multi.projects in .orchid.yaml"
            )
            raise typer.Exit(1)
        _cmd_multi(projects, code_model=code_model)

    elif action == "status":
        console.print(
            "[yellow]'multi status' requires a running instance.[/yellow]\n"
            "Start one with: orchid multi start --project ~/a --project ~/b"
        )

    elif action == "stop":
        console.print("[yellow]Use Ctrl+C to stop a running 'multi start' instance.[/yellow]")

    else:
        console.print(f"[red]Unknown action '{action}'. Use: start | status | stop[/red]")
        raise typer.Exit(1)


@app.command()
def telegram(
    project: Optional[List[str]] = typer.Option(
        None, "--project", "-p",
        help="Project directory. Repeat with --multi for multi-project mode.",
    ),
    token: Optional[str] = typer.Option(None, "--token", help="Telegram bot token (overrides env)"),
    multi: bool = typer.Option(False, "--multi", help="Multi-project mode — tag all notifications with project name"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
) -> None:
    """Start the Telegram bot interface for a project.

    Single-project:  orchid telegram --project ~/myapp
    Multi-project:   orchid telegram --multi --project ~/a --project ~/b
    """
    _setup_logging(log_level)
    import os

    resolved_token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not resolved_token:
        console.print(
            "[red]No Telegram bot token found.[/red]\n"
            "Set [bold]TELEGRAM_BOT_TOKEN[/bold] in your .env, or pass [bold]--token[/bold].\n"
            "Create a bot via @BotFather on Telegram to get a token."
        )
        raise typer.Exit(1)

    raw_users = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    allowed_users: list[int] = []
    for uid in raw_users.split(","):
        uid = uid.strip()
        if uid.isdigit():
            allowed_users.append(int(uid))

    resolved_projects = [str(_resolve_project(p)) for p in (project or ["."])]
    primary_project = resolved_projects[0]
    extra_projects = resolved_projects[1:] if multi and len(resolved_projects) > 1 else []

    try:
        from orchid.interfaces.telegram_bot import TelegramBot
    except ImportError as exc:
        console.print(f"[red]Failed to import telegram_bot: {exc}[/red]")
        console.print("Run: [bold]uv pip install 'python-telegram-bot>=20.0'[/bold]")
        raise typer.Exit(1)

    if multi:
        all_projects = resolved_projects
        console.print(f"[green]Starting Telegram bot (multi-project):[/green]")
        for p in all_projects:
            console.print(f"  • {p}")
    else:
        console.print(f"[green]Starting Telegram bot for project: {primary_project}[/green]")

    bot = TelegramBot(
        project_path=primary_project,
        token=resolved_token,
        allowed_users=allowed_users,
        multi_project=multi,
        extra_projects=extra_projects or None,
    )
    console.print("[dim]Press Ctrl+C to stop.[/dim]")
    try:
        bot.start()
    except KeyboardInterrupt:
        bot.stop()
        console.print("[dim]Bot stopped.[/dim]")


@app.command()
def slack(
    project: Optional[List[str]] = typer.Option(
        None, "--project", "-p",
        help="Project directory. Repeat with --multi for multi-project mode.",
    ),
    bot_token: Optional[str] = typer.Option(None, "--token", help="Slack bot token xoxb- (overrides env)"),
    app_token: Optional[str] = typer.Option(None, "--app-token", help="Slack app token xapp- (overrides env)"),
    channel: Optional[str] = typer.Option(None, "--channel", help="Default Slack channel (overrides env)"),
    multi: bool = typer.Option(False, "--multi", help="Multi-project mode — tag all notifications with project name"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
) -> None:
    """Start the Slack bot interface for a project (Socket Mode).

    Single-project:  orchid slack --project ~/myapp
    Multi-project:   orchid slack --multi --project ~/a --project ~/b

    Requires: SLACK_BOT_TOKEN (xoxb-) and SLACK_APP_TOKEN (xapp-) in .env.
    Create your app at https://api.slack.com/apps with Socket Mode enabled.
    """
    _setup_logging(log_level)
    import os

    resolved_bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
    resolved_app_token = app_token or os.environ.get("SLACK_APP_TOKEN", "")
    resolved_channel = channel or os.environ.get("SLACK_DEFAULT_CHANNEL", "")

    if not resolved_bot_token:
        console.print(
            "[red]No Slack bot token found.[/red]\n"
            "Set [bold]SLACK_BOT_TOKEN[/bold] (xoxb-...) in your .env, or pass [bold]--token[/bold].\n"
            "Create a Slack app at https://api.slack.com/apps with Socket Mode enabled."
        )
        raise typer.Exit(1)

    if not resolved_app_token:
        console.print(
            "[red]No Slack app token found.[/red]\n"
            "Set [bold]SLACK_APP_TOKEN[/bold] (xapp-...) in your .env, or pass [bold]--app-token[/bold].\n"
            "Enable Socket Mode in your Slack app to get this token."
        )
        raise typer.Exit(1)

    resolved_projects = [str(_resolve_project(p)) for p in (project or ["."])]
    primary_project = resolved_projects[0]
    extra_projects = resolved_projects[1:] if multi and len(resolved_projects) > 1 else []

    try:
        from orchid.interfaces.slack_bot import SlackBot
    except ImportError as exc:
        console.print(f"[red]Failed to import slack_bot: {exc}[/red]")
        console.print("Run: [bold]uv pip install 'slack-bolt>=1.18.0'[/bold]")
        raise typer.Exit(1)

    if multi:
        console.print("[green]Starting Slack bot (multi-project):[/green]")
        for p in resolved_projects:
            console.print(f"  • {p}")
    else:
        console.print(f"[green]Starting Slack bot for project: {primary_project}[/green]")

    bot = SlackBot(
        project_path=primary_project,
        bot_token=resolved_bot_token,
        app_token=resolved_app_token,
        default_channel=resolved_channel,
        multi_project=multi,
        extra_projects=extra_projects or None,
    )
    console.print("[dim]Press Ctrl+C to stop.[/dim]")
    try:
        bot.start()
    except KeyboardInterrupt:
        bot.stop()
        console.print("[dim]Bot stopped.[/dim]")


@app.command()
def web(
    project: Optional[List[str]] = typer.Option(
        None, "--project", "-p",
        help="Project directory. Repeat for multi-project mode.",
    ),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(7842, "--port", help="Bind port"),
    dev: bool = typer.Option(False, "--dev", help="Dev mode: enable reload"),
    log_level: str = typer.Option("info", "--log-level", "-l"),
) -> None:
    """Start the Orchid web server (FastAPI + React UI).

    Single-project:  orchid web --project ~/myapp
    Multi-project:   orchid web --project ~/a --project ~/b
    Dev mode:        orchid web --project . --dev

    UI at http://localhost:7842  (or configured host:port)
    Traefik: routes orchid.scheidy.com → localhost:7842
    """
    _setup_logging(log_level.upper())

    try:
        from orchid.interfaces.web_server import serve
    except ImportError as exc:
        console.print(f"[red]Failed to import web_server: {exc}[/red]")
        console.print(
            "Run: [bold]uv pip install 'fastapi>=0.110.0' 'uvicorn[standard]>=0.27.0' 'websockets>=12.0'[/bold]"
        )
        raise typer.Exit(1)

    resolved_projects = [str(_resolve_project(p)) for p in (project or ["."])]
    console.print(
        f"[green]Starting Orchid web server on {host}:{port}[/green]\n"
        + "\n".join(f"  • {p}" for p in resolved_projects)
        + ("\n  [dim]Dev mode enabled[/dim]" if dev else "")
    )
    console.print(f"  UI: [cyan]http://{'localhost' if host == '0.0.0.0' else host}:{port}[/cyan]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]")

    serve(
        project_paths=resolved_projects,
        host=host,
        port=port,
        dev=dev,
        log_level=log_level,
    )


if __name__ == "__main__":
    app()
