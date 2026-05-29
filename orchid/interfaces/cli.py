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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchid.lifecycle import ProjectLifecycle
    from orchid.session import Session

from dotenv import load_dotenv

load_dotenv()
from pathlib import Path
load_dotenv(Path.home() / ".config" / "orchid" / ".env", override=False)

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table


def _git_info() -> tuple[str, str]:
    """Return (short_hash, commit_time) from the local git repo, or ('', '') on failure."""
    import subprocess
    try:
        hash_ = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        time_ = subprocess.check_output(
            ["git", "log", "-1", "--format=%ci", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return hash_, time_
    except Exception:
        return "", ""


def _version_callback(value: bool) -> None:
    if value:
        try:
            version = importlib.metadata.version("orchid")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown (package not installed)"
        git_hash, git_time = _git_info()
        if git_hash:
            console.print(f"orchid {version} (commit {git_hash}, {git_time})")
        else:
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


def _make_session(project: str) -> Session:
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
    version: bool | None = typer.Option(
        None, "--version", "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    project: list[str] | None = typer.Option(
        None, "--project", "-p",
        help="Path to the project directory. Repeat for --multi mode.",
    ),
    multi: bool = typer.Option(False, "--multi", help="Run multiple projects in parallel"),
    mode: str | None = typer.Option(
        None, "--mode", "-m",
        help="Run mode: auto (autonomous) | interactive (chat)",
    ),
    output_format: str = typer.Option(
        "rich", "--output-format", "-o",
        help="Output format: rich (default) | stream-json (NDJSON events to stdout)",
    ),
    status: bool = typer.Option(False, "--status", "-s", help="Show task board and hot memory"),
    add_task: str | None = typer.Option(
        None, "--add-task", "-a",
        help="Add a new task (title string). Use --type and --priority to customise.",
    ),
    task_type: str = typer.Option("draft", "--type", help="Task type for --add-task"),
    priority: int = typer.Option(2, "--priority", help="Priority for --add-task (1=high)"),
    max_tasks: int = typer.Option(50, "--max-tasks", "-n", help="Max tasks for auto mode"),
    recall: str | None = typer.Option(
        None, "--recall", help="Query vector memory and print top results",
    ),
    search: str | None = typer.Option(
        None, "--search", help="Run a web search and print results (embeds if vector enabled)",
    ),
    code_model: str | None = typer.Option(
        None, "--code-model",
        help="Force model for all tasks: claude | local | auto",
    ),
    provider: list[str] | None = typer.Option(
        None, "--provider",
        help="Per-agent-type provider override: agent=provider (e.g. developer=ollama). Repeatable.",
    ),
    offline: bool = typer.Option(
        False, "--offline",
        help="Offline mode: route all agents to the local provider.",
    ),
    check_providers: bool = typer.Option(
        False, "--check-providers",
        help="Probe all configured providers and print their availability status.",
    ),
    tail: bool = typer.Option(False, "--tail", help="Tail the most recent live agent log"),
    inject: str | None = typer.Option(
        None, "--inject", help="Inject context into the running agent via inject.queue",
    ),
    get_result: str | None = typer.Option(
        None, "--get-result", metavar="TASK_ID",
        help="Print stored result for a task ID (e.g. T090). Useful for debugging rollup inputs.",
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i",
        help="Start interactive planning/discussion session (V2 lifecycle).",
    ),
    approve: bool = typer.Option(
        False, "--approve",
        help="Approve the current lifecycle gate to advance to the next phase.",
    ),
    auto_approve: bool = typer.Option(
        False, "--auto",
        help="With --approve: also set future gates to auto for this session.",
    ),
    phase: bool = typer.Option(
        False, "--phase",
        help="Show current lifecycle phase and what is needed to advance.",
    ),
    artifacts: bool = typer.Option(
        False, "--artifacts",
        help="List generated lifecycle artifacts with existence status.",
    ),
    run_task: str | None = typer.Option(
        None, "--run-task",
        metavar="TASK_ID",
        help="Run a single specific task by ID (e.g. T015), ignoring queue order.",
    ),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
    trace: bool = typer.Option(
        False, "--trace",
        help="Write per-iteration ReAct trace to <project>/.orchid/trace.log.",
    ),
    rewind: str | None = typer.Option(
        None, "--rewind",
        metavar="CHECKPOINT_ID",
        help="Restore session state from a saved checkpoint. The session is overwritten"
             " with the checkpoint contents (tasks, hot memory, decisions, delegations)."
             "Use --list-checkpoints to see available IDs. The session is NOT persisted"
             "to disk — run --mode auto or --interactive after rewinding to continue.",
    ),
    resume: str | None = typer.Option(
        None, "--resume",
        metavar="CHECKPOINT_ID",
        help="Restore session state from a checkpoint AND auto-approve the current gate"
             "to advance to the next phase. Combines --rewind and --approve behaviour."
             "Use --list-checkpoints to see available IDs.",
    ),
    list_checkpoints: bool = typer.Option(
        False, "--list-checkpoints",
        help="List all saved checkpoints for the project with IDs, timestamps, and task IDs.",
    ),
) -> None:
    if ctx.invoked_subcommand:
        return

    _setup_logging(log_level)

    # Resolve project path(s); default to cwd when none provided
    resolved_projects = [str(_resolve_project(p)) for p in (project or ["."])]
    proj = resolved_projects[0]

    # When --project is not given, validate that cwd is an Orchid project
    _any_project_command = any([
        status, recall is not None, search is not None, add_task, tail,
        inject is not None, get_result is not None, phase, artifacts,
        approve, interactive, run_task is not None, mode is not None,
        rewind is not None, resume is not None, list_checkpoints,
    ])
    if project is None and _any_project_command:
        if not (Path(proj) / ".orchid.yaml").exists():
            console.print(
                f"[red]No .orchid.yaml found in {proj!r}.[/red]\n"
                "Run [bold]orchid init .[/bold] to initialise a project here, "
                "or use [bold]--project <path>[/bold] to specify one."
            )
            raise typer.Exit(1)

    if check_providers:
        _cmd_check_providers(proj=proj)
        return

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

    if get_result is not None:
        _cmd_get_result(proj, get_result)
        return

    if phase:
        _cmd_phase(proj)
        return

    if artifacts:
        _cmd_artifacts(proj)
        return

    if approve:
        _cmd_approve(proj, auto=auto_approve)
        return

    if interactive:
        _cmd_interactive_planning(
            proj,
            provider_overrides={p.split("=", 1)[0].strip(): p.split("=", 1)[1].strip()
                                 for p in (provider or []) if "=" in p},
            offline=offline,
        )
        return

    if run_task is not None:
        _cmd_run_task(proj, run_task, code_model=code_model, offline=offline, trace=trace)
        return

    if list_checkpoints:
        _cmd_list_checkpoints(proj)
        return

    if rewind is not None:
        _cmd_rewind(proj, rewind)
        return

    if resume is not None:
        _cmd_resume(proj, resume)
        return

    # Parse --provider agent=provider pairs
    provider_overrides: dict[str, str] = {}
    for p in (provider or []):
        if "=" in p:
            agent_name, provider_name = p.split("=", 1)
            provider_overrides[agent_name.strip()] = provider_name.strip()
        else:
            console.print(f"[yellow]Ignoring malformed --provider value: {p!r} (expected agent=provider)[/yellow]")

    if mode == "auto":
        _cmd_auto(
            proj, max_tasks,
            code_model=code_model,
            provider_overrides=provider_overrides,
            offline=offline,
            trace=trace,
            output_format=output_format,
        )
    elif mode == "interactive":
        _cmd_interactive(proj, model=code_model)
    else:
        # No flags given — show help
        console.print(ctx.get_help())


# ── Mode implementations ──────────────────────────────────────────────────────


def _cmd_auto(
    project: str,
    max_tasks: int,
    code_model: str | None = None,
    provider_overrides: dict[str, str] | None = None,
    offline: bool = False,
    trace: bool = False,
    output_format: str = "rich",
) -> None:
    """Autonomous mode — run all pending tasks."""
    session = _make_session(project)
    from orchid.memory.state import TaskStatus
    pending = [t for t in session.tasks if t.status == TaskStatus.TODO]

    model_note = ""
    if offline:
        model_note = "  [yellow]Offline mode — all agents using local provider[/yellow]"
    elif provider_overrides:
        model_note = "  Providers: " + ", ".join(f"{a}={p}" for a, p in provider_overrides.items())
    elif code_model:
        model_note = f"  Model override: {code_model}"

    console.print(Panel(
        f"[bold green]Orchid — Autonomous Mode[/bold green]\n"
        f"Project: [cyan]{session.project_name}[/cyan]"
        + (f"\n{session.project_description}" if session.project_description else "")
        + f"\nTasks pending: {len(pending)}"
        + (f"\n{model_note}" if model_note else ""),
        border_style="green",
    ))

    if not pending:
        console.print("[yellow]No pending tasks. Add tasks to tasks.md and rerun.[/yellow]")
        raise typer.Exit()

    from orchid.orchestrator import Orchestrator
    from orchid.output.emitter import NullEmitter
    from orchid.output.ndjson_emitter import NDJSONEmitter

    stream_emitter: Any = NullEmitter()
    if output_format == "stream-json":
        stream_emitter = NDJSONEmitter()

    # Phase 2+3: load CLI session for vault injection and budget recording
    from orchid.interfaces.cli_auth import load_cli_session
    cli_session = load_cli_session()
    cli_user_id = cli_session["user_id"] if cli_session else None

    if cli_user_id:
        try:
            from orchid.budget.guard import BudgetExceededError, BudgetGuard
            BudgetGuard(cli_user_id).check()
            BudgetGuard(cli_user_id).check_cpu()
        except Exception as _be:
            console.print(f"[red]Budget check failed: {_be}[/red]")
            raise typer.Exit(1)

    orch = Orchestrator(
        session,
        cli_model_override=code_model,
        cli_provider_overrides=provider_overrides,
        offline_mode=offline,
        trace_enabled=trace,
        stream_emitter=stream_emitter,
    )
    if trace:
        trace_path = session.project_dir / ".orchid" / "trace.log"
        console.print(f"[dim]Trace → {trace_path}[/dim]")
    if cli_user_id:
        console.print(f"[dim]Running as user: {cli_session['username']} ({cli_user_id})[/dim]")

    cost_before = _snapshot_ledger_cost(project)
    import time as _time
    wall_start = _time.monotonic()
    try:
        if cli_user_id:
            from orchid.budget.guard import vault_env_context
            with vault_env_context(cli_user_id):
                orch.run_loop(max_tasks=max_tasks)
        else:
            orch.run_loop(max_tasks=max_tasks)
    finally:
        wall_elapsed = _time.monotonic() - wall_start
        if cli_user_id:
            _record_run_cost(cli_user_id, project, cost_before, wall_elapsed)
        session.close(summary="Autonomous run complete.")
        console.print("[green]Session saved.[/green]")


def _cmd_run_task(
    project: str,
    task_id: str,
    code_model: str | None = None,
    offline: bool = False,
    trace: bool = False,
) -> None:
    """Run a single specific task by ID, ignoring queue order."""
    session = _make_session(project)
    task = next((t for t in session.tasks if t.id == task_id), None)
    if task is None:
        console.print(f"[red]Task {task_id} not found[/red]")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold green]Orchid — Single Task Run[/bold green]\n"
        f"Project: [cyan]{session.project_name}[/cyan]\n"
        f"Task: [bold]{task.id}[/bold] {task.title}\n"
        f"Status: {task.status.value}",
        border_style="green",
    ))

    from orchid.interfaces.cli_auth import load_cli_session
    cli_session = load_cli_session()
    cli_user_id = cli_session["user_id"] if cli_session else None

    if cli_user_id:
        try:
            from orchid.budget.guard import BudgetExceededError, BudgetGuard
            BudgetGuard(cli_user_id).check()
            BudgetGuard(cli_user_id).check_cpu()
        except Exception as _be:
            console.print(f"[red]Budget check failed: {_be}[/red]")
            raise typer.Exit(1)

    from orchid.orchestrator import Orchestrator

    orch = Orchestrator(session, cli_model_override=code_model, offline_mode=offline, trace_enabled=trace)
    if trace:
        trace_path = session.project_dir / ".orchid" / "trace.log"
        console.print(f"[dim]Trace → {trace_path}[/dim]")

    cost_before = _snapshot_ledger_cost(project)
    import time as _time
    wall_start = _time.monotonic()
    try:
        if cli_user_id:
            from orchid.budget.guard import vault_env_context
            with vault_env_context(cli_user_id):
                result = orch._execute_task(task)
        else:
            result = orch._execute_task(task)
        session.save()
        snippet = str(result.get("result", ""))[:300] if result else ""
        console.print(f"[green]✓ {task.id} complete[/green]")
        if snippet:
            console.print(f"[dim]{snippet}[/dim]")
    except Exception as exc:
        console.print(f"[red]Task {task.id} failed: {exc}[/red]")
        raise typer.Exit(1)
    finally:
        wall_elapsed = _time.monotonic() - wall_start
        if cli_user_id:
            _record_run_cost(cli_user_id, project, cost_before, wall_elapsed)
        session.close(summary=f"Single task run: {task_id}")


def _cmd_interactive(project: str, model: str | None = None) -> None:
    """Interactive chat mode."""
    session = _make_session(project)
    from orchid.agents.base import BaseAgent
    from orchid.providers.registry import get_registry as _get_reg

    resolved = model or _get_reg().resolve_name(agent_type="base")

    class _ChatAgent(BaseAgent):
        model_key = resolved

    agent = _ChatAgent(session_context=session.context_block())

    console.print(Panel(
        f"[bold cyan]Orchid — Interactive Mode[/bold cyan]\n"
        f"Project: [cyan]{session.project_name}[/cyan]  |  Model: {resolved}\n"
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

    # TODO
    # One real limitation worth knowing: there's no conversation history between messages. 
    # Each agent.run(user_input) is a fresh ReAct call. The agent has your project context 
    # (CLAUDE.md, hot memory) but not what you asked 2 turns ago. So:
    #
    # - "What does T001 do?" → works fine                                                     
    # - "And what about T002?" → the agent has no memory of the previous question
    #
    # If you want to add conversation continuity, the fix would be accumulating a messages:
    # list[Message] across the loop and passing it into each agent.run() call. 
    # But that's a real feature addition — the current implementation is functional 
    # for standalone questions about your project. 
    # Try it as-is first and see if the stateless behaviour is actually a problem in practice.


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


def _load_last_failures(proj_path) -> dict[str, str]:
    """Return {task_id: reason} from task_failed events in the most recent session log."""
    import json as _json
    log_dir = proj_path / ".orchid" / "session_logs"
    if not log_dir.exists():
        return {}
    logs = sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return {}
    failures: dict[str, str] = {}
    try:
        for line in logs[0].read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = _json.loads(line)
            if rec.get("type") == "task_failed":
                tid = rec.get("task_id", "")
                if tid:
                    failures[tid] = rec.get("reason", "failed")
    except Exception:
        pass
    return failures


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

    failures = _load_last_failures(proj_path)

    from orchid.memory.state import TaskStatus
    completed_ids = {t.id for t in session.tasks if t.status == TaskStatus.DONE}
    for t in session.tasks:
        color = status_color.get(t.status.value, "white")
        deps_str = ""
        if t.depends_on:
            waiting = [d for d in t.depends_on if d not in completed_ids]
            deps_str = ",".join(t.depends_on)
            if waiting:
                deps_str = f"⏳ {','.join(waiting)}"
        warn = " ⚠" if t.id in failures else ""
        table.add_row(
            t.id,
            f"[{color}]{t.status.value}{warn}[/{color}]",
            escape(t.title),
            t.type,
            str(t.priority),
            deps_str,
        )

    console.print(table)

    # Warn about tasks that failed in the last run
    if failures:
        lines = []
        for tid, reason in failures.items():
            short = reason[:120].replace("\n", " ")
            lines.append(f"[cyan]{tid}[/cyan]  {escape(short)}")
        console.print(Panel(
            "\n".join(lines),
            title="[bold red]⚠ Failed in last run[/bold red]",
            border_style="red",
        ))

    delegation_count = _count_last_session_delegations(str(proj_path))
    summary_parts = [
        f"tasks={len(session.tasks)}",
        f"decisions={len(session.decisions)}",
        f"delegations={delegation_count}",
    ]
    if failures:
        summary_parts.append(f"[red]failed={len(failures)}[/red]")
    console.print(f"[dim]{' · '.join(summary_parts)}[/dim]")

    if session.hot_memory:
        console.print(Panel(
            Markdown(session.hot_memory[:2000]),
            title="[bold]Hot Memory (CLAUDE.md)[/bold]",
            border_style="blue",
        ))


def _cmd_search(project: str, query: str) -> None:
    """Run a web search and print results."""
    from rich.markup import escape  # noqa: PLC0415

    from orchid import config as cfg  # noqa: PLC0415
    from orchid.tools.search import WebSearchTool, reset_backend_cache  # noqa: PLC0415

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
        console.print("[dim]Results embedded into vector store.[/dim]")


def _cmd_recall(project: str, query: str) -> None:
    """Query vector memory and pretty-print results."""
    from orchid import config as cfg  # noqa: PLC0415
    from orchid.memory.vector import VectorMemory  # noqa: PLC0415

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
    console.print("[dim]Agent will pick this up on next ReAct iteration.[/dim]")


def _snapshot_ledger_cost(project: str) -> float:
    """Read total cost from the on-disk ledger before a run (for delta calculation)."""
    try:
        from orchid.cost.ledger import CostLedger
        return CostLedger(_resolve_project(project)).get_totals()["total_cost_usd"]
    except Exception:
        return 0.0


def _record_run_cost(user_id: str, project: str, cost_before: float, wall_elapsed: float) -> None:
    """Record cost delta and CPU time to BudgetGuard after a run. Never raises."""
    try:
        from orchid.budget.guard import BudgetGuard
        from orchid.cost.ledger import get_cost_ledger
        cost_after = get_cost_ledger().get_totals()["total_cost_usd"]
        run_cost = max(0.0, cost_after - cost_before)
        guard = BudgetGuard(user_id)
        if run_cost > 0:
            guard.record(run_cost)
        if wall_elapsed > 0:
            guard.record_cpu(wall_elapsed)
    except Exception:
        pass


def _cmd_check_providers(proj: str | None = None) -> None:
    """Probe all configured providers and print their status."""
    from orchid.providers.registry import get_registry, reset_registry

    if proj:
        from orchid import config as cfg
        cfg.configure_for_project(_resolve_project(proj))
        reset_registry()

    registry = get_registry()
    statuses = registry.all_status()

    table = Table(title="Provider Status", show_lines=True)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Type", style="dim")
    table.add_column("Status", style="bold")
    table.add_column("Detail")

    for entry in statuses:
        avail = entry["available"]
        status_str = "[green]available[/green]" if avail else "[red]unavailable[/red]"
        detail = ""
        if not avail:
            detail = entry.get("missing", "")
            fix = entry.get("fix", "")
            if fix:
                detail += f"\n  [dim]Fix: {fix}[/dim]"
        table.add_row(entry["name"], entry["type"], status_str, detail)

    console.print(table)


def _cmd_add_task(project: str, title: str, task_type: str, priority: int) -> None:
    """Add a task directly from the CLI."""
    session = _make_session(project)
    from orchid.memory.state import Task, save_tasks

    tid = f"T{len(session.tasks) + 1:03d}"
    t = Task(id=tid, title=title, type=task_type, priority=priority)
    session.tasks.append(t)
    save_tasks(session.tasks, project)
    console.print(f"[green]Added {tid}: {title} (type={task_type}, p{priority})[/green]")


def _cmd_get_result(project: str, task_id: str) -> None:
    """Print the stored result for a specific task ID."""
    from rich.markup import escape

    from orchid.memory.state import TaskResultStore

    proj_path = _resolve_project(project)
    store = TaskResultStore(proj_path)
    entry = store.get(task_id)
    if entry is None:
        console.print(f"[yellow]No stored result for {task_id}[/yellow]")
        raise typer.Exit(1)
    console.print(Panel(
        f"[bold]{escape(entry['title'])}[/bold]\n"
        f"[dim]type={entry['type']}  completed={entry['completed_at'][:19]}[/dim]\n\n"
        + escape(entry["result"]),
        title=f"[cyan]{task_id}[/cyan]",
        border_style="cyan",
    ))


def _cmd_phase(project: str) -> None:
    """Show current lifecycle phase and what's needed to advance."""
    from orchid.lifecycle import ProjectLifecycle

    proj_path = _resolve_project(project)
    lc = ProjectLifecycle.load(proj_path)
    phase = lc.current_phase()
    next_phases = lc.valid_next_phases()
    artifacts_ok = lc.artifacts_complete()

    console.print(Panel(
        f"[bold]Phase:[/bold] [cyan]{phase}[/cyan]\n"
        f"[bold]Project:[/bold] {lc.state.project_name}\n"
        f"[bold]Artifacts complete:[/bold] {'[green]yes[/green]' if artifacts_ok else '[yellow]no[/yellow]'}\n"
        f"[bold]Can advance to:[/bold] {', '.join(next_phases) if next_phases else 'none'}",
        title="[bold]Lifecycle Phase[/bold]",
        border_style="cyan",
    ))


def _cmd_artifacts(project: str) -> None:
    """List lifecycle artifacts with existence status."""
    proj_path = _resolve_project(project)

    artifact_names = [
        "REQUIREMENTS.md", "ARCHITECTURE.md", "MILESTONES.md", "tasks.md",
        ".orchid/discussion/conversation.jsonl", ".orchid/discussion/context.md",
        ".orchid/project.state.json",
    ]
    table = Table(title="Lifecycle Artifacts", show_lines=True)
    table.add_column("File", style="cyan")
    table.add_column("Status")

    for name in artifact_names:
        exists = (proj_path / name).exists()
        status = "[green]exists[/green]" if exists else "[dim]missing[/dim]"
        table.add_row(name, status)

    console.print(table)


def _cmd_approve(project: str, auto: bool = False) -> None:
    """Approve the current lifecycle gate."""
    from orchid.gates import GateStatus, GateSystem
    from orchid.lifecycle import ProjectLifecycle

    proj_path = _resolve_project(project)
    lc = ProjectLifecycle.load(proj_path)
    gates = GateSystem(lc)

    current = lc.current_phase()
    next_phases = [p for p in lc.valid_next_phases() if p != "DISCUSSING"]
    if not next_phases:
        console.print(f"[yellow]No non-discussion transitions available from {current}.[/yellow]")
        raise typer.Exit(1)

    # Approve the first (primary) non-discussion next phase
    to_phase = next_phases[0]
    status = gates.check_gate(to_phase)
    if status == GateStatus.BLOCKED:
        console.print(
            f"[red]Gate BLOCKED — prerequisites not met for {current} → {to_phase}.[/red]\n"
            f"Run [bold]orchid --project {project} --artifacts[/bold] to see what's missing."
        )
        raise typer.Exit(1)

    gates.approve(to_phase)

    if auto:
        # Mark remaining gates as auto for this project
        remaining = lc.valid_next_phases()
        for p in remaining:
            key = lc._transition_key(to_phase, p)
            lc.state.gates.setdefault(key, {})["type"] = "auto"
        lc.save()

    # Advance the lifecycle
    try:
        lc.advance(to_phase)
        console.print(
            f"[green]Approved.[/green] Phase advanced: [cyan]{current}[/cyan] → [bold cyan]{to_phase}[/bold cyan]"
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)



def _cmd_list_checkpoints(project: str) -> None:
    """List all saved checkpoints for the project."""
    from rich.markup import escape
    from rich.table import Table

    from orchid.checkpoint.restore import list_checkpoints

    entries = list_checkpoints(project)
    if not entries:
        console.print('[yellow]No checkpoints found for this project.[/yellow]')
        return

    table = Table(title='Saved Checkpoints', show_lines=True)
    table.add_column('ID', style='cyan', no_wrap=True)
    table.add_column('Task', style='dim')
    table.add_column('Created', style='dim')
    table.add_column('Size', justify='right')

    for e in entries:
        ts = e.created_at[:19].replace('T', ' ') if e.created_at else ''
        size_str = f'{e.size_bytes:,} B' if e.size_bytes else '-'
        task_str = escape(e.task_id) if e.task_id else '-'
        table.add_row(e.checkpoint_id, task_str, ts, size_str)

    console.print(table)
    console.print(f'[dim]{len(entries)} checkpoint(s) total. Use --rewind <ID> to restore.[/dim]')


def _cmd_rewind(project: str, checkpoint_id: str) -> None:
    """Restore session state from a checkpoint."""
    from rich.markup import escape

    from orchid.checkpoint.restore import rewind_session

    console.print(Panel(
        f'[bold]Rewinding session to checkpoint:[/bold] [cyan]{escape(checkpoint_id)}[/cyan]',
        border_style='yellow',
    ))

    result = rewind_session(project, checkpoint_id)
    if result is None:
        console.print(f'[red]Checkpoint {checkpoint_id} not found.[/red]')
        console.print('[dim]Run --list-checkpoints to see available IDs.[/dim]')
        raise typer.Exit(1)

    console.print(f'[green]✓[/green] Rewound to checkpoint {checkpoint_id} (task={result.metadata.task_id})')
    console.print('[dim]Session state restored. Run --mode auto or --interactive to continue.[/dim]')


def _cmd_resume(project: str, checkpoint_id: str) -> None:
    """Restore session from a checkpoint AND auto-approve the current gate."""
    from rich.markup import escape

    from orchid.checkpoint.restore import rewind_session
    from orchid.gates import GateStatus, GateSystem
    from orchid.lifecycle import ProjectLifecycle

    console.print(Panel(
        f'[bold]Resuming session from checkpoint:[/bold] [cyan]{escape(checkpoint_id)}[/cyan]',
        border_style='yellow',
    ))

    result = rewind_session(project, checkpoint_id)
    if result is None:
        console.print(f'[red]Checkpoint {checkpoint_id} not found.[/red]')
        console.print('[dim]Run --list-checkpoints to see available IDs.[/dim]')
        raise typer.Exit(1)

    console.print(f'[green]✓[/green] Rewound to checkpoint {checkpoint_id} (task={result.metadata.task_id})')

    # Auto-approve the current gate
    proj_path = _resolve_project(project)
    lc = ProjectLifecycle.load(proj_path)
    current = lc.current_phase()
    next_phases = [p for p in lc.valid_next_phases() if p != 'DISCUSSING']

    if not next_phases:
        console.print(f'[yellow]No non-discussion transitions available from {current}.[/yellow]')
        raise typer.Exit(1)

    gates = GateSystem(lc)
    to_phase = next_phases[0]
    gate_status = gates.check_gate(to_phase)
    if gate_status == GateStatus.BLOCKED:
        console.print(
            f"[red]Gate BLOCKED — prerequisites not met for {current} → {to_phase}.[/red]\n"
            f"Run [bold]orchid --project {project} --artifacts[/bold] to see what is missing."
        )
        raise typer.Exit(1)

    gates.approve(to_phase)

    try:
        lc.advance(to_phase)
        console.print(
            f'[green]✓[/green] Approved. Phase advanced: [cyan]{current}[/cyan] → [bold cyan]{to_phase}[/bold cyan]'
        )
    except ValueError as exc:
        console.print(f'[red]Error: {exc}[/red]')
        raise typer.Exit(1)

    console.print('[dim]Session resumed. Run --mode auto or --interactive to continue.[/dim]')


def _cmd_interactive_planning(
    project: str,
    provider_overrides: dict[str, str] | None = None,
    offline: bool = False,
) -> None:
    """Interactive planning session — routes to appropriate agent based on phase."""
    from orchid.gates import GateStatus, GateSystem
    from orchid.lifecycle import ProjectLifecycle

    proj_path = _resolve_project(project)
    lc = ProjectLifecycle.load(proj_path)
    phase = lc.current_phase()

    po = provider_overrides or {}
    disc_override = po.get("discussion")
    pm_override = po.get("product_manager")
    pmgr_override = po.get("project_manager")

    if phase in ("NEW", "DISCUSSING"):
        _run_discussion_loop(proj_path, lc, disc_override=disc_override, offline=offline)

    elif phase == "REQUIREMENTS":
        console.print(Panel(
            "[bold]Phase:[/bold] REQUIREMENTS\n"
            "Generating REQUIREMENTS.md and ARCHITECTURE.md...",
            border_style="yellow",
        ))
        from orchid.agents.product_manager import ProductManagerAgent
        agent = ProductManagerAgent(proj_path, cli_override=pm_override, offline=offline)
        result = agent.run()
        console.print(f"[green]Generated:[/green] {result.requirements_path.name}, {result.architecture_path.name}")
        lc.advance("PLANNING")
        gates = GateSystem(lc)
        gate_status = gates.check_gate("READY")
        if gate_status == GateStatus.WAITING:
            gates.notify_gate_reached("READY")
            console.print("[yellow]Gate: awaiting approval for PLANNING → READY.[/yellow]")
            console.print(f"[dim]Run: orchid --project {project} --approve[/dim]")

    elif phase == "PLANNING":
        console.print(Panel(
            "[bold]Phase:[/bold] PLANNING\nGenerating MILESTONES.md and tasks.md...",
            border_style="yellow",
        ))
        from orchid.agents.project_manager import ProjectManagerAgent
        agent = ProjectManagerAgent(proj_path, cli_override=pmgr_override, offline=offline)
        result = agent.run()
        console.print(
            f"[green]Generated:[/green] {result.milestones_path.name}, {result.tasks_path.name}"
            f" ({result.task_count} tasks)"
        )
        lc.advance("READY")
        gates = GateSystem(lc)
        gate_status = gates.check_gate("EXECUTING")
        if gate_status == GateStatus.WAITING:
            gates.notify_gate_reached("EXECUTING")
            console.print("[yellow]Gate: awaiting approval for READY → EXECUTING.[/yellow]")
            console.print(f"[dim]Run: orchid --project {project} --approve[/dim]")

    elif phase == "READY":
        _show_ready_summary(proj_path, project)

    elif phase == "EXECUTING":
        _cmd_status(project)
        console.print("[dim]Phase EXECUTING. Use --mode auto to run tasks.[/dim]")

    else:
        console.print(f"[dim]Phase: {phase}. Nothing to do interactively.[/dim]")


def _run_discussion_loop(
    proj_path: Path,
    lc: ProjectLifecycle,
    disc_override: str | None = None,
    offline: bool = False,
) -> None:
    """Run the interactive discussion loop until user exits or agent signals readiness."""
    from orchid.agents.discussion_agent import DiscussionAgent
    from orchid.discussion import DiscussionHistory

    history = DiscussionHistory.load(proj_path)
    agent = DiscussionAgent(proj_path, cli_override=disc_override, offline=offline)

    if lc.current_phase() == "NEW":
        lc.advance("DISCUSSING")

    turns = history.turn_count()
    console.print(Panel(
        f"[bold cyan]Orchid — Requirements Discussion[/bold cyan]\n"
        f"Project: [cyan]{lc.state.project_name}[/cyan]"
        + (f"  •  {turns} turns so far" if turns else "  •  New conversation")
        + "\n\nDescribe what you want to build. Type [bold]done[/bold] when ready, [bold]exit[/bold] to quit.",
        border_style="cyan",
    ))

    # Show existing context if any
    ctx = history.get_context_md()
    from orchid.discussion import _DEFAULT_CONTEXT_MD
    if ctx.strip() != _DEFAULT_CONTEXT_MD.strip():
        console.print(Panel(ctx[:1500], title="[bold]Captured Context[/bold]", border_style="blue"))

    while True:
        try:
            user_input = Prompt.ask("\n[bold blue]You[/bold blue]")
        except (EOFError, KeyboardInterrupt):
            break
        stripped = user_input.strip().lower()
        if stripped in {"exit", "quit", "q"}:
            break
        if stripped in {"done", "advance", "ready"}:
            console.print("[dim]Signalling readiness...[/dim]")
            _try_advance_from_discussion(proj_path, lc)
            break

        history.append("user", user_input)
        lc.state.discussion_turns += 1
        lc.save()

        try:
            response = agent.run(user_input, history)
        except Exception as exc:
            console.print(f"[red]Agent error: {exc}[/red]")
            continue

        history.append("agent", response.message)
        if response.context_updates:
            agent.update_context(history, response.context_updates)

        console.print(Panel(
            Markdown(response.message),
            title="[bold green]Orchid[/bold green]",
            border_style="green",
        ))

        if response.suggestions:
            console.print("[dim]Suggestions:[/dim]")
            for s in response.suggestions[:3]:
                console.print(f"  [dim]• {s}[/dim]")

        if response.ready_to_advance:
            console.print(
                "[bold green]Requirements look complete![/bold green] "
                "Type [bold]done[/bold] to generate artifacts, or continue discussing."
            )

    console.print("[dim]Discussion saved.[/dim]")


def _try_advance_from_discussion(proj_path: Path, lc: ProjectLifecycle) -> None:
    """After discussion, generate PM artifacts and advance lifecycle."""
    from orchid.agents.product_manager import ProductManagerAgent
    from orchid.agents.project_manager import ProjectManagerAgent
    from orchid.gates import GateStatus, GateSystem

    project = str(proj_path)

    # Requirements → Planning
    console.print("[dim]Generating REQUIREMENTS.md and ARCHITECTURE.md...[/dim]")
    pm = ProductManagerAgent(proj_path)
    result = pm.run()
    console.print(f"[green]✓[/green] {result.requirements_path.name}")
    console.print(f"[green]✓[/green] {result.architecture_path.name}")
    lc.advance("REQUIREMENTS")

    gates = GateSystem(lc)
    gate_status = gates.check_gate("PLANNING")
    if gate_status == GateStatus.WAITING:
        console.print(
            "[yellow]Gate: REQUIREMENTS → PLANNING requires approval.[/yellow]\n"
            f"[dim]orchid --project {project} --approve[/dim]"
        )
        return

    # Planning → Ready
    console.print("[dim]Generating MILESTONES.md and tasks.md...[/dim]")
    lc.advance("PLANNING")
    pmgr = ProjectManagerAgent(proj_path)
    result2 = pmgr.run()
    console.print(f"[green]✓[/green] {result2.milestones_path.name}")
    console.print(f"[green]✓[/green] {result2.tasks_path.name} ({result2.task_count} tasks)")
    lc.advance("READY")

    gates2 = GateSystem(lc)
    gate_status2 = gates2.check_gate("EXECUTING")
    if gate_status2 == GateStatus.WAITING:
        gates2.notify_gate_reached("EXECUTING")
        console.print(
            "[bold green]Project is READY![/bold green] Awaiting approval to start execution.\n"
            f"[dim]orchid --project {project} --approve[/dim]"
        )
    else:
        console.print("[bold green]Project is READY![/bold green]")
        console.print(f"[dim]orchid --project {project} --mode auto[/dim]")


def _show_ready_summary(proj_path: Path, project: str) -> None:
    """Show summary of generated artifacts when in READY phase."""
    artifacts = ["REQUIREMENTS.md", "ARCHITECTURE.md", "MILESTONES.md", "tasks.md"]
    lines = []
    for name in artifacts:
        p = proj_path / name
        if p.exists():
            lines.append(f"[green]✓[/green]  {name}")
        else:
            lines.append(f"[yellow]✗[/yellow]  {name} (missing)")
    console.print(Panel(
        "\n".join(lines) + "\n\n[dim]Approve to begin execution:[/dim]\n"
        f"[bold]orchid --project {project} --approve[/bold]",
        title="[bold green]Project READY[/bold green]",
        border_style="green",
    ))


def _cmd_multi(projects: list[str], code_model: str | None = None) -> None:
    """Run multiple projects in parallel worker processes."""
    from orchid.interfaces.multi_formatter import format_notification as fmt_multi
    from orchid.multi import MultiOrchid

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
    name: str | None = typer.Option(None, "--name", "-n", help="Project name (defaults to dirname)"),
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
    console.print("  Edit [cyan]CLAUDE.md[/cyan] and [cyan].orchid.yaml[/cyan], then add tasks and run:")
    console.print(f"  [bold]orchid --project {path} --mode auto[/bold]")


@app.command(name="new")
def new_project(
    description: str = typer.Argument(..., help="What you want to build (short description)"),
    name: str | None = typer.Option(None, "--name", "-n", help="Project name (defaults to slug of description)"),
    dir_path: str | None = typer.Option(None, "--dir", help="Base directory (overrides machine-profile)"),
    project_type: str | None = typer.Option(
        None, "--type", "-t",
        help="Project type for directory routing: ai | web | tool | game",
    ),
    no_interactive: bool = typer.Option(False, "--no-interactive", help="Skip discussion after creation"),
    provider: list[str] | None = typer.Option(
        None, "--provider",
        help="Per-agent provider override, e.g. discussion=ollama. Repeatable.",
    ),
    offline: bool = typer.Option(False, "--offline", help="Offline mode: use local provider"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
) -> None:
    """Create a new Orchid project and drop into interactive planning.

    Examples:
      orchid new "A simple bookmark manager"
      orchid new "Recipe sharing app" --name recipes --type web
      orchid new "CLI tool for git stats" --type tool --no-interactive
    """
    _setup_logging(log_level)
    from orchid.machine_profile import MachineProfile
    from orchid.project_creator import ProjectCreator

    profile = MachineProfile.load()
    creator = ProjectCreator(machine_profile=profile)

    # Derive project name from description if not provided
    project_name = name or _slugify(description)

    base_dir = Path(dir_path).expanduser() if dir_path else None
    suggested = (
        (base_dir / project_name) if base_dir
        else creator.confirm_path(project_name, project_type)
    )

    console.print(f"\nCreate [bold cyan]{suggested}[/bold cyan]? ", end="")
    confirm = Prompt.ask("", choices=["y", "n"], default="y")
    if confirm != "y":
        console.print("[dim]Aborted.[/dim]")
        raise typer.Exit()

    project_dir = creator.create(
        name=project_name,
        description=description,
        project_type=project_type,
        base_dir=base_dir,
    )

    console.print(f"[green]Created project:[/green] {project_dir}")

    if no_interactive:
        console.print(f"[dim]Start discussion with: orchid --project {project_dir} --interactive[/dim]")
        return

    po: dict[str, str] = {}
    for p in (provider or []):
        if "=" in p:
            k, v = p.split("=", 1)
            po[k.strip()] = v.strip()

    _cmd_interactive_planning(str(project_dir), provider_overrides=po, offline=offline)


def _slugify(text: str) -> str:
    """Convert a description to a filesystem-safe project name."""
    import re as _re
    slug = _re.sub(r"[^a-z0-9]+", "-", text.lower().strip()).strip("-")
    return slug[:40] or "project"


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
    action: str = typer.Argument(..., help="add | done | block | cancel | skip"),
    project: str = typer.Option(".", "--project", "-p"),
    task_id: str | None = typer.Option(None, "--id"),
    title: str = typer.Argument("", help="Task title (required for add)"),
    task_type: str = typer.Option("draft", "--type"),
    priority: int = typer.Option(2, "--priority"),
    description: str = typer.Option("", "--desc", "-d"),
) -> None:
    """Manage tasks: add, mark done, block, cancel, or skip."""
    session = _make_session(project)
    from orchid.memory.state import Task, TaskStatus, save_tasks

    if action == "add":
        if not title:
            console.print("[red]title required for add action (use: orchid task add --title '...' --id T001)[/red]")
            raise typer.Exit(1)
        tid = task_id or f"T{len(session.tasks) + 1:03d}"
        t = Task(id=tid, title=title, type=task_type, priority=priority, description=description)
        session.tasks.append(t)
        save_tasks(session.tasks, project)
        console.print(f"[green]Added {tid}: {title}[/green]")

    elif action in ("done", "block", "cancel", "skip"):
        if not task_id:
            console.print("[red]--id required[/red]")
            raise typer.Exit(1)
        status_map = {
            "done": TaskStatus.DONE,
            "block": TaskStatus.BLOCKED,
            "cancel": TaskStatus.CANCELLED,
            "skip": TaskStatus.SKIPPED,
        }
        new_status = status_map[action]
        if session.update_task_status(task_id, new_status):
            save_tasks(session.tasks, project)
            console.print(f"[green]Marked {task_id} as {new_status.value}[/green]")
        else:
            console.print(f"[red]Task {task_id} not found[/red]")
            raise typer.Exit(1)

    else:
        console.print(f"[red]Unknown action: {action}. Use add|done|block|cancel|skip[/red]")
        raise typer.Exit(1)


@app.command(name="multi")
def multi_cmd(
    action: str = typer.Argument("start", help="start | status | stop"),
    project: list[str] | None = typer.Option(
        None, "--project", "-p",
        help="Project directory (repeat for multiple projects)",
    ),
    code_model: str | None = typer.Option(
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
    project: list[str] | None = typer.Option(
        None, "--project", "-p",
        help="Project directory. Repeat with --multi for multi-project mode.",
    ),
    token: str | None = typer.Option(None, "--token", help="Telegram bot token (overrides env)"),
    multi: bool = typer.Option(False, "--multi", help="Multi-project mode — tag all notifications with project name"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
) -> None:
    """[DEPRECATED] Start the Telegram bot interface for a project.

    Single-project:  orchid telegram --project ~/myapp
    Multi-project:   orchid telegram --multi --project ~/a --project ~/b

    DEPRECATED: Use 'orchid serve --telegram' instead for the central multi-project bot.
    """
    console.print(
        "[yellow]⚠️  DEPRECATED:[/yellow] 'orchid telegram' is deprecated.\n"
        "Use [bold]orchid serve --telegram[/bold] for the new central multi-project Telegram bot.\n"
        "Continuing with legacy single-project mode…\n"
    )
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
        console.print("[green]Starting Telegram bot (multi-project):[/green]")
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
    project: list[str] | None = typer.Option(
        None, "--project", "-p",
        help="Project directory. Repeat with --multi for multi-project mode.",
    ),
    bot_token: str | None = typer.Option(None, "--token", help="Slack bot token xoxb- (overrides env)"),
    app_token: str | None = typer.Option(None, "--app-token", help="Slack app token xapp- (overrides env)"),
    channel: str | None = typer.Option(None, "--channel", help="Default Slack channel (overrides env)"),
    multi: bool = typer.Option(False, "--multi", help="Multi-project mode — tag all notifications with project name"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
) -> None:
    """[DEPRECATED] Start the Slack bot interface for a project (Socket Mode).

    Single-project:  orchid slack --project ~/myapp
    Multi-project:   orchid slack --multi --project ~/a --project ~/b

    Requires: SLACK_BOT_TOKEN (xoxb-) and SLACK_APP_TOKEN (xapp-) in .env.
    Create your app at https://api.slack.com/apps with Socket Mode enabled.

    DEPRECATED: Use 'orchid serve --slack' instead for the new central multi-project bot.
    """
    console.print(
        "[yellow]⚠️  DEPRECATED:[/yellow] 'orchid slack' is deprecated.\n"
        "Use [bold]orchid serve --slack[/bold] for the new central multi-project Slack bot.\n"
        "Continuing with legacy single-project mode…\n"
    )
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
    project: list[str] | None = typer.Option(
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


@app.command()
def serve(
    watch_dir: list[str] | None = typer.Option(
        None, "--watch-dir",
        help="Directory to watch for orchid projects (repeatable). Default: ~/LocalAI",
    ),
    project: list[str] | None = typer.Option(
        None, "--project", "-p",
        help="Explicit project path (repeatable). Always registered, .orchid.yaml optional.",
    ),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(7842, "--port", help="Bind port"),
    log_level: str = typer.Option("info", "--log-level", "-l"),
    enable_telegram: bool = typer.Option(False, "--telegram", help="Enable central Telegram bot (requires TELEGRAM_BOT_TOKEN)"),
    enable_slack: bool = typer.Option(False, "--slack", help="Enable central Slack bot (requires SLACK_BOT_TOKEN + SLACK_APP_TOKEN)"),
    enable_bots: bool = typer.Option(False, "--bots", help="Enable both Telegram and Slack bots"),
) -> None:
    """Persistent multi-project server with auto-discovery.

    Scans watch dirs for orchid projects, starts the web UI, and watches
    for new/removed projects. Ideal for running as a systemd service.

    Examples:
      orchid serve --watch-dir ~/LocalAI
      orchid serve --watch-dir ~/LocalAI --watch-dir ~/projects --port 7842
      orchid serve --watch-dir ~/LocalAI --project ~/other/myproj
      orchid serve --watch-dir ~/LocalAI --bots
      orchid serve --watch-dir ~/LocalAI --telegram
      orchid serve --watch-dir ~/LocalAI --slack
    """
    _setup_logging(log_level.upper())

    try:
        from orchid.interfaces.web_server import serve as _serve
    except ImportError as exc:
        console.print(f"[red]Failed to import web_server: {exc}[/red]")
        raise typer.Exit(1)

    from orchid import config as cfg

    # Resolve watch dirs (fall back to config default)
    resolved_watch_dirs: list[str] = []
    if watch_dir:
        resolved_watch_dirs = [str(_resolve_project(d)) for d in watch_dir]
    else:
        default_dirs = cfg.get("serve.watch_dirs", [])
        if default_dirs:
            resolved_watch_dirs = [str(_resolve_project(d)) for d in default_dirs]
        else:
            # Final fallback: ~/LocalAI
            fallback = Path("~/LocalAI").expanduser()
            resolved_watch_dirs = [str(fallback)]

    # Explicit projects provided via --project
    resolved_projects = [str(_resolve_project(p)) for p in (project or [])]

    # --bots enables both
    run_telegram = enable_telegram or enable_bots
    run_slack = enable_slack or enable_bots

    console.print(
        f"[green]Starting Orchid persistent server on {host}:{port}[/green]"
    )
    if resolved_watch_dirs:
        console.print("[dim]Watching for projects in:[/dim]")
        for d in resolved_watch_dirs:
            console.print(f"  • {d}")
    if resolved_projects:
        console.print("[dim]Explicit projects:[/dim]")
        for p in resolved_projects:
            console.print(f"  • {p}")
    if run_telegram:
        console.print("  🤖 [cyan]Telegram bot enabled[/cyan]")
    if run_slack:
        console.print("  🤖 [cyan]Slack bot enabled[/cyan]")
    console.print(f"  UI: [cyan]http://{'localhost' if host == '0.0.0.0' else host}:{port}[/cyan]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]")

    _serve(
        project_paths=resolved_projects,
        host=host,
        port=port,
        log_level=log_level,
        watch_dirs=resolved_watch_dirs or None,
        enable_telegram=run_telegram,
        enable_slack=run_slack,
    )



# ── login / logout / whoami ───────────────────────────────────────────────────

@app.command()
def login(
    server: str = typer.Option("http://localhost:7842", "--server", "-s", help="Orchid server URL"),
    username: str | None = typer.Option(None, "--username", "-u", help="Username"),
    log_level: str = typer.Option("WARNING", "--log-level", "-l"),
) -> None:
    """Authenticate with an Orchid server and save the session locally.

    Saves credentials to ~/.config/orchid/cli_session.json (mode 0600).
    Required for vault injection, budget enforcement, and per-user MCP catalog.
    """
    import time as _time

    import httpx
    from rich.prompt import Prompt

    from orchid.interfaces.cli_auth import DEFAULT_SERVER_URL, save_cli_session

    _setup_logging(log_level)
    base = server.rstrip("/")

    if not username:
        username = Prompt.ask("[bold blue]Username[/bold blue]")
    password = Prompt.ask("[bold blue]Password[/bold blue]", password=True)

    try:
        resp = httpx.post(
            f"{base}/api/auth/login",
            json={"username": username, "password": password},
            timeout=15,
        )
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to {base}. Is orchid serve running?[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Login request failed: {exc}[/red]")
        raise typer.Exit(1)

    if resp.status_code == 401:
        console.print("[red]Invalid credentials.[/red]")
        raise typer.Exit(1)
    if resp.status_code != 200:
        console.print(f"[red]Login failed: HTTP {resp.status_code}[/red]")
        raise typer.Exit(1)

    data = resp.json()
    refresh_token = resp.cookies.get("orchid_refresh", "")

    session = {
        "user_id": data["user_id"],
        "username": data["username"],
        "role": data.get("role", "user"),
        "access_token": data["access_token"],
        "refresh_token": refresh_token,
        "server_url": base,
        "issued_at": _time.time(),
    }
    save_cli_session(session)

    console.print(
        f"[green]Logged in as[/green] [bold]{data['username']}[/bold]  "
        f"role: {data.get('role', 'user')}  [dim]{base}[/dim]"
    )
    if not refresh_token:
        console.print("[yellow]Warning: no refresh token in response. Session will not auto-renew.[/yellow]")


@app.command()
def logout() -> None:
    """Revoke the current CLI session and delete the local session file."""
    import httpx

    from orchid.interfaces.cli_auth import clear_cli_session, load_cli_session

    session = load_cli_session()
    if session is None:
        console.print("[yellow]No active session.[/yellow]")
        return

    base = session.get("server_url", "http://localhost:7842").rstrip("/")
    refresh_token = session.get("refresh_token", "")
    access_token = session.get("access_token", "")

    try:
        httpx.post(
            f"{base}/api/auth/logout",
            json={"refresh_token": refresh_token} if refresh_token else {},
            headers={"Authorization": f"Bearer {access_token}"} if access_token else {},
            timeout=10,
        )
    except Exception:
        pass  # Server may be down; always clear local session regardless

    clear_cli_session()
    console.print("[green]Logged out.[/green]  [dim](session cleared)[/dim]")


@app.command()
def whoami() -> None:
    """Show the currently authenticated CLI user."""
    import httpx

    from orchid.interfaces.cli_auth import get_valid_session

    session = get_valid_session()
    if session is None:
        console.print("[yellow]Not logged in. Run: orchid login[/yellow]")
        raise typer.Exit(1)

    base = session.get("server_url", "http://localhost:7842").rstrip("/")
    access_token = session.get("access_token", "")

    try:
        resp = httpx.get(
            f"{base}/api/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    except Exception:
        console.print(
            f"[bold]{session['username']}[/bold]  "
            f"role: {session.get('role', '?')}  "
            f"[dim](server unreachable — showing cached info)[/dim]"
        )
        return

    if resp.status_code == 200:
        user = resp.json()
        console.print(
            f"[bold]{user.get('username', session['username'])}[/bold]\n"
            f"  user_id:  {user.get('user_id', session['user_id'])}\n"
            f"  role:     {user.get('role', session.get('role', '?'))}\n"
            f"  email:    {user.get('email') or '—'}\n"
            f"  server:   [dim]{base}[/dim]"
        )
    else:
        console.print(
            f"[yellow]Session may be expired (HTTP {resp.status_code}). "
            f"Try: orchid login[/yellow]"
        )


# ── migrate-to-postgres ───────────────────────────────────────────────────────

@app.command(name="migrate-to-postgres")
def migrate_to_postgres(
    dsn: str = typer.Option(
        ...,
        "--dsn",
        envvar="ORCHID_AUTH_STORE_DSN",
        help="Postgres DSN: postgresql://user:pass@host/db",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print what would be migrated without writing to Postgres",
    ),
) -> None:
    """Migrate users, tokens, and keys from FileUserStore → PostgresUserStore.

    Reads the JSON store at ~/.config/orchid/users.json and copies all records
    to the target Postgres database. Existing rows are skipped (not overwritten).

    Example:
      orchid migrate-to-postgres --dsn postgresql://orchid:orchid_dev@localhost/orchid
      ORCHID_AUTH_STORE_DSN=... orchid migrate-to-postgres
    """
    from orchid.auth.store import FileUserStore

    try:
        import psycopg2  # noqa: F401
    except ImportError:
        console.print("[red]psycopg2 not installed. Run: uv pip install 'orchid[postgres]'[/red]")
        raise typer.Exit(1)

    from orchid.auth.store_postgres import PostgresUserStore

    users_json = Path("~/.config/orchid/users.json").expanduser()
    if not users_json.exists():
        console.print(f"[yellow]No FileUserStore found at {users_json} — nothing to migrate.[/yellow]")
        raise typer.Exit(0)

    source = FileUserStore(path=users_json)

    if dry_run:
        console.print(f"[dim]DRY RUN — reading from {users_json}, no writes to Postgres[/dim]")
        target = None
    else:
        try:
            target = PostgresUserStore(dsn, minconn=1, maxconn=3)
        except Exception as exc:
            console.print(f"[red]Cannot connect to Postgres: {exc}[/red]")
            raise typer.Exit(1)

    users = source.list_users()
    console.print(f"Found [bold]{len(users)}[/bold] users in FileUserStore")

    migrated_users = 0
    skipped_users = 0
    migrated_keys = 0
    migrated_tasks = 0

    for u in users:
        if dry_run:
            console.print(f"  [cyan]user[/cyan] {u.user_id!r} ({u.username})")
            # Count their API keys from user.api_keys dict
            migrated_keys += len(u.api_keys)
            migrated_tasks += len(u.scheduled_tasks)
            migrated_users += 1
            continue

        # Users
        try:
            target.add_user(u)
            migrated_users += 1
            console.print(f"  ✓ user {u.user_id!r} ({u.username})")
        except Exception as exc:
            # AuthError = already exists; skip gracefully
            skipped_users += 1
            console.print(f"  [yellow]skip[/yellow] user {u.user_id!r}: {exc}")
            continue

        # API keys (stored inside user.api_keys dict: {key_id: ApiKey-dict})
        from orchid.auth.types import ApiKey
        from datetime import datetime
        for key_id, ak_data in u.api_keys.items():
            try:
                if isinstance(ak_data, dict):
                    ak = ApiKey(
                        key_id=ak_data.get("key_id", key_id),
                        secret_hash=ak_data.get("secret_hash", ""),
                        user_id=u.user_id,
                        name=ak_data.get("name", ""),
                        scopes=ak_data.get("scopes", []),
                        created_at=datetime.fromisoformat(ak_data["created_at"]) if ak_data.get("created_at") else datetime.now(),
                        is_active=ak_data.get("is_active", True),
                    )
                else:
                    ak = ak_data
                    ak.user_id = u.user_id
                target.store_api_key(ak)
                migrated_keys += 1
            except Exception as exc:
                console.print(f"    [yellow]skip[/yellow] api_key {key_id!r}: {exc}")

        # Scheduled tasks (stored as list of dicts in user.scheduled_tasks)
        for task_dict in (u.scheduled_tasks or []):
            try:
                if isinstance(task_dict, dict) and "task_id" in task_dict:
                    target.upsert_scheduled_task(u.user_id, task_dict)
                    migrated_tasks += 1
            except Exception as exc:
                console.print(f"    [yellow]skip[/yellow] task {task_dict.get('task_id')!r}: {exc}")

    action = "Would migrate" if dry_run else "Migrated"
    console.print(
        f"\n[green]{action}[/green]: "
        f"{migrated_users} users, {migrated_keys} API keys, {migrated_tasks} scheduled tasks. "
        f"Skipped: {skipped_users} existing users."
    )
    if not dry_run:
        console.print(
            f"\n[dim]Set ORCHID_AUTH_STORE_DSN={dsn!r} to use Postgres going forward.[/dim]"
        )


# ── MCP sub-app ───────────────────────────────────────────────────────────────

mcp_app = typer.Typer(
    name="mcp",
    help="Manage MCP (Model Context Protocol) servers and tools.",
)


@mcp_app.command()
def ls(
    project: str = typer.Option(".", "--project", "-p", help="Project directory"),
) -> None:
    """List all tools available from configured MCP servers."""
    from rich.markup import escape

    from orchid import config as cfg
    from orchid.interfaces.cli_auth import load_cli_session
    from orchid.mcp.manager import MCPManager

    cfg.configure_for_project(_resolve_project(project))
    manager = MCPManager()

    cli_session = load_cli_session()
    if cli_session:
        vault_store = None
        try:
            from orchid.vault.store import get_vault
            vault_store = get_vault()
        except Exception:
            pass
        manager.connect_for_user(
            user_id=cli_session["user_id"],
            user_role=cli_session.get("role", "user"),
            vault_store=vault_store,
            users_dir=Path("~/.config/orchid/users").expanduser(),
        )
        console.print(f"[dim]Showing servers for user: {cli_session['username']}[/dim]")
    else:
        manager.discover_servers()
        manager.connect()

    try:
        tools = manager.list_tools()
        if not tools:
            console.print("[yellow]No MCP tools found.[/yellow]")
            return

        table = Table(title="MCP Tools", show_lines=True)
        table.add_column("Server", style="cyan", no_wrap=True)
        table.add_column("Tool", style="bold")
        table.add_column("Description", style="dim")

        by_server = manager.list_tools_by_server()
        for server_name, server_tools in by_server.items():
            for tool in server_tools:
                desc = escape(tool.description)[:120] if tool.description else ""
                table.add_row(server_name, tool.name, desc)

        console.print(table)
    finally:
        manager.disconnect()


@mcp_app.command()
def call(
    project: str = typer.Option(".", "--project", "-p", help="Project directory"),
    tool: str = typer.Argument(..., help="Tool name to call"),
    arguments: str = typer.Option("", "--arg", "-a",
                                  help="JSON string of arguments (e.g. '{\"msg\":\"hello\"}')"),
) -> None:
    """Call an MCP tool and print its result."""
    import json

    from rich.markup import escape

    from orchid import config as cfg
    from orchid.interfaces.cli_auth import load_cli_session
    from orchid.mcp.manager import MCPManager

    cfg.configure_for_project(_resolve_project(project))
    manager = MCPManager()

    cli_session = load_cli_session()
    if cli_session:
        vault_store = None
        try:
            from orchid.vault.store import get_vault
            vault_store = get_vault()
        except Exception:
            pass
        manager.connect_for_user(
            user_id=cli_session["user_id"],
            user_role=cli_session.get("role", "user"),
            vault_store=vault_store,
            users_dir=Path("~/.config/orchid/users").expanduser(),
        )
    else:
        manager.discover_servers()
        manager.connect()

    try:
        args: dict = {}
        if arguments:
            try:
                args = json.loads(arguments)
            except json.JSONDecodeError as exc:
                console.print(f"[red]Invalid JSON for --arg: {exc}[/red]")
                raise typer.Exit(1)

        result = manager.call_tool(tool, args)
        content = result.content if isinstance(result.content, str) else json.dumps(result.content, indent=2)
        console.print(Panel(
            escape(content),
            title=f"[cyan]{tool}[/cyan]",
            border_style="green",
        ))
    except Exception as exc:
        console.print(f"[red]Tool call failed: {exc}[/red]")
        raise typer.Exit(1)
    finally:
        manager.disconnect()


app.add_typer(mcp_app)

# Register hooks CLI subcommands
try:
    from orchid.interfaces.hooks_cli import register_hooks_cli
    register_hooks_cli(app)
except ImportError:
    pass

if __name__ == "__main__":
    app()
