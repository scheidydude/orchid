"""CLI entry point — interactive and autonomous modes."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

app = typer.Typer(
    name="orchid",
    help="Orchid AI agent orchestration framework",
    no_args_is_help=True,
)
console = Console()


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


# ── Commands ──────────────────────────────────────────────────────────────────


@app.command()
def run(
    project: str = typer.Argument(".", help="Path to the project directory"),
    max_tasks: int = typer.Option(50, "--max-tasks", "-n", help="Max tasks per session"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
) -> None:
    """Run the orchestrator autonomously until all tasks are done."""
    _setup_logging(log_level)
    session = _make_session(project)
    pending = [t for t in session.tasks if t.status.value == "TODO"]
    console.print(Panel(
        f"[bold green]Orchid — Autonomous Mode[/bold green]\n"
        f"Project: {Path(project).resolve().name}\n"
        f"Tasks pending: {len(pending)}",
        border_style="green",
    ))

    if not pending:
        console.print("[yellow]No pending tasks. Add tasks to tasks.md and rerun.[/yellow]")
        raise typer.Exit()

    from orchid.orchestrator import Orchestrator
    orch = Orchestrator(session)
    try:
        orch.run_loop(max_tasks=max_tasks)
    finally:
        session.close(summary="Autonomous run complete.")
        console.print("[green]Session saved.[/green]")


@app.command()
def chat(
    project: str = typer.Argument(".", help="Path to the project directory"),
    model: str = typer.Option("claude", "--model", "-m", help="Model key: claude or local"),
    log_level: str = typer.Option("WARNING", "--log-level", "-l"),
) -> None:
    """Interactive chat with an agent in the context of a project."""
    _setup_logging(log_level)
    session = _make_session(project)

    from orchid.agents.base import BaseAgent

    class _ChatAgent(BaseAgent):
        model_key = model

    agent = _ChatAgent(session_context=session.context_block())

    console.print(Panel(
        f"[bold cyan]Orchid — Interactive Chat[/bold cyan]\n"
        f"Project: {Path(project).resolve().name}  |  Model: {model}\n"
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

    session.close(summary="Interactive chat session.")
    console.print("[dim]Session saved. Goodbye.[/dim]")


@app.command()
def status(
    project: str = typer.Argument(".", help="Path to the project directory"),
) -> None:
    """Show current project state: tasks and hot memory summary."""
    session = _make_session(project)

    from orchid.memory.state import TaskStatus
    from rich.table import Table

    table = Table(title=f"Tasks — {Path(project).resolve().name}", show_lines=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", style="bold")
    table.add_column("Title")
    table.add_column("Type", style="dim")
    table.add_column("P", justify="center")

    status_color = {
        "TODO": "white", "IN_PROGRESS": "yellow", "DONE": "green",
        "BLOCKED": "red", "CANCELLED": "dim",
    }

    for t in session.tasks:
        color = status_color.get(t.status.value, "white")
        table.add_row(t.id, f"[{color}]{t.status.value}[/{color}]", t.title, t.type, str(t.priority))

    console.print(table)

    if session.hot_memory:
        console.print(Panel(
            Markdown(session.hot_memory[:2000]),
            title="[bold]Hot Memory (CLAUDE.md)[/bold]",
            border_style="blue",
        ))


@app.command()
def task(
    action: str = typer.Argument(..., help="add | done | block"),
    project: str = typer.Option(".", "--project", "-p"),
    task_id: Optional[str] = typer.Option(None, "--id"),
    title: str = typer.Option("", "--title", "-t"),
    task_type: str = typer.Option("draft", "--type"),
    priority: int = typer.Option(2, "--priority"),
    description: str = typer.Option("", "--desc", "-d"),
) -> None:
    """Manage tasks: add, mark done, or block."""
    session = _make_session(project)

    from orchid.memory.state import Task, TaskStatus, save_tasks

    if action == "add":
        tid = task_id or f"T{len(session.tasks) + 1:03d}"
        t = Task(id=tid, title=title, type=task_type, priority=priority, description=description)
        session.tasks.append(t)
        save_tasks(session.tasks, project)
        console.print(f"[green]Added task {tid}: {title}[/green]")

    elif action == "done":
        if not task_id:
            console.print("[red]--id required[/red]")
            raise typer.Exit(1)
        if session.update_task_status(task_id, TaskStatus.DONE):
            save_tasks(session.tasks, project)
            console.print(f"[green]Marked {task_id} as DONE[/green]")
        else:
            console.print(f"[red]Task {task_id} not found[/red]")

    elif action == "block":
        if not task_id:
            console.print("[red]--id required[/red]")
            raise typer.Exit(1)
        if session.update_task_status(task_id, TaskStatus.BLOCKED):
            save_tasks(session.tasks, project)
            console.print(f"[yellow]Marked {task_id} as BLOCKED[/yellow]")
        else:
            console.print(f"[red]Task {task_id} not found[/red]")

    else:
        console.print(f"[red]Unknown action: {action}. Use add|done|block[/red]")
        raise typer.Exit(1)


@app.command()
def decide(
    title: str = typer.Argument(..., help="Short decision title"),
    decision: str = typer.Option(..., "--decision", "-d"),
    rationale: str = typer.Option("", "--rationale", "-r"),
    project: str = typer.Option(".", "--project", "-p"),
) -> None:
    """Record an architectural decision."""
    from orchid.memory.decisions import record_decision
    rec = record_decision(title, decision, rationale, project_dir=project)
    console.print(f"[green]Recorded {rec['id']}: {title}[/green]")


if __name__ == "__main__":
    app()
