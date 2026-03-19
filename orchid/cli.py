"""CLI entry point for Orchid."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_project_config(project_path: Path) -> dict:
    """Load .orchid.yaml if present."""
    config_file = project_path / ".orchid.yaml"
    if not config_file.exists():
        return {}
    try:
        import yaml
        with config_file.open() as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load .orchid.yaml: %s", e)
        return {}


def _get_provider(args, config: dict):
    """Instantiate the appropriate provider based on args and config."""
    from .providers import AnthropicProvider, OllamaProvider, LlamaCppProvider

    # Determine model preference
    model_pref = getattr(args, 'model', None) or config.get('model', 'auto')

    if model_pref == 'claude' or (model_pref == 'auto' and not getattr(args, 'offline', False)):
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if api_key:
            return AnthropicProvider(api_key=api_key)

    # Fall back to local
    local_backend = config.get('local_backend', 'llamacpp')
    if local_backend == 'ollama':
        return OllamaProvider()
    return LlamaCppProvider()


async def _run_auto(args) -> None:
    """Run in autonomous mode."""
    from .tasks import TaskManager
    from .session import SessionManager
    from .agent import AgentLoop
    from .routing import TaskRouter

    project_path = Path(args.project).resolve()
    config = _load_project_config(project_path)

    provider = _get_provider(args, config)
    session = SessionManager(str(project_path))
    task_mgr = TaskManager(str(project_path))
    router = TaskRouter(config)

    pending = task_mgr.get_pending_tasks()
    if not pending:
        print("No pending tasks found.")
        return

    print(f"Found {len(pending)} pending task(s). Starting autonomous run...")
    session.log_event("session_start", tasks_total=len(pending))

    delegations_total = 0
    start_time = asyncio.get_event_loop().time()

    for task in pending:
        task_id = task.get("id", "unknown")
        title = task.get("title", "")
        task_type = task.get("type", "draft")

        # Route to appropriate model
        routed_provider, routing_info = router.route(task, provider)

        session.log_event(
            "task_start",
            task_id=task_id,
            title=title,
            model=routing_info.get("model", "unknown"),
            routing_reason=routing_info.get("reason", ""),
            routing_source=routing_info.get("source", ""),
        )

        print(f"\n[{task_id}] {title}")

        agent = AgentLoop(
            provider=routed_provider,
            session=session,
            project_path=str(project_path),
            agent_type=_task_type_to_agent(task_type),
        )

        result = agent.run_task(title)
        delegations_total += agent.delegations

        task_mgr.mark_done(task_id, result)
        session.log_event("task_done", task_id=task_id, result=result[:500] if result else "", delegations=agent.delegations)
        print(f"  ✓ Done: {result[:100] if result else '(no result)'}")

    # ------------------------------------------------------------------
    # End-of-session: run reviewer agent's import consistency check
    # ------------------------------------------------------------------
    _run_end_of_session_review(provider, session, str(project_path))

    tasks_done = len(pending)
    duration = asyncio.get_event_loop().time() - start_time
    session.log_event(
        "session_end",
        summary="Autonomous run complete.",
        duration_seconds=round(duration, 2),
        tasks_done=tasks_done,
        tasks_total=tasks_done,
        delegations_total=delegations_total,
    )
    print(f"\n✓ Completed {tasks_done} task(s).")


def _run_end_of_session_review(provider, session, project_path: str) -> None:
    """Spawn a reviewer agent and run the end-of-session import check."""
    from .agent import AgentLoop

    print("\n[review] Running end-of-session import consistency check...")
    reviewer = AgentLoop(
        provider=provider,
        session=session,
        project_path=project_path,
        agent_type="reviewer",
    )
    result = reviewer.run_end_of_session_review()
    print(f"  {result[:200]}")


def _task_type_to_agent(task_type: str) -> str:
    """Map task type to agent type."""
    mapping = {
        "code_generate": "developer",
        "review": "reviewer",
        "research": "researcher",
        "draft": "developer",
        "summarize": "base",
        "plan": "developer",
        "critique": "reviewer",
        "synthesize": "developer",
        "search": "researcher",
        "orchestrate": "developer",
    }
    return mapping.get(task_type, "developer")


async def _run_interactive(args) -> None:
    """Run in interactive mode."""
    print("Interactive mode not yet implemented. Use --mode auto.")


async def _run_status(args) -> None:
    """Show project status."""
    from .tasks import TaskManager

    project_path = Path(args.project).resolve()
    task_mgr = TaskManager(str(project_path))

    all_tasks = task_mgr.get_all_tasks()
    pending = [t for t in all_tasks if t.get("status") == "pending"]
    done = [t for t in all_tasks if t.get("status") == "done"]
    in_progress = [t for t in all_tasks if t.get("status") == "in_progress"]

    print(f"Project: {project_path}")
    print(f"Tasks: {len(done)} done, {len(in_progress)} in progress, {len(pending)} pending")

    if pending:
        print("\nPending tasks:")
        for t in pending[:10]:
            print(f"  [{t['id']}] {t['title'][:60]}")


async def _add_task(args) -> None:
    """Add a new task."""
    from .tasks import TaskManager

    project_path = Path(args.project).resolve()
    task_mgr = TaskManager(str(project_path))
    task_mgr.add_task(args.add_task)
    print(f"Task added: {args.add_task}")


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="orchid",
        description="Orchid AI agent orchestration framework",
    )

    parser.add_argument("--project", "-p", help="Path to project directory")
    parser.add_argument("--mode", choices=["auto", "interactive"], default="auto")
    parser.add_argument("--model", choices=["claude", "local", "auto"], default="auto")
    parser.add_argument("--offline", action="store_true", help="Use local models only")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--status", action="store_true", help="Show project status")
    parser.add_argument("--add-task", metavar="TASK", help="Add a new task")
    parser.add_argument("--recall", metavar="QUERY", help="Search session memory")
    parser.add_argument("--search", metavar="QUERY", help="Search project files")
    parser.add_argument("--tail", action="store_true", help="Tail session logs")
    parser.add_argument("--inject", metavar="TEXT", help="Inject text into running session")
    parser.add_argument("--check-providers", action="store_true", help="Check provider availability")
    parser.add_argument("--multi", action="store_true", help="Multi-project mode")
    parser.add_argument("init", nargs="?", help="Initialize a new project")

    args = parser.parse_args()
    _setup_logging(args.verbose)

    if args.check_providers:
        _check_providers()
        return

    if not args.project and args.init:
        _init_project(args.init)
        return

    if not args.project:
        parser.print_help()
        return

    if args.status:
        asyncio.run(_run_status(args))
    elif args.add_task:
        asyncio.run(_add_task(args))
    elif args.mode == "interactive":
        asyncio.run(_run_interactive(args))
    else:
        asyncio.run(_run_auto(args))


def _check_providers() -> None:
    """Check which providers are available."""
    from .providers import AnthropicProvider, OllamaProvider, LlamaCppProvider
    import os

    print("Checking providers...")

    # Anthropic
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if api_key:
        print("  ✓ Anthropic (API key found)")
    else:
        print("  ✗ Anthropic (no ANTHROPIC_API_KEY)")

    # LlamaCpp
    try:
        import requests
        r = requests.get("http://localhost:8080/health", timeout=2)
        if r.status_code == 200:
            print("  ✓ LlamaCpp (running on :8080)")
        else:
            print("  ✗ LlamaCpp (not healthy)")
    except Exception:
        print("  ✗ LlamaCpp (not running)")

    # Ollama
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            print("  ✓ Ollama (running on :11434)")
        else:
            print("  ✗ Ollama (not healthy)")
    except Exception:
        print("  ✗ Ollama (not running)")


def _init_project(path: str) -> None:
    """Initialize a new Orchid project."""
    project_path = Path(path).resolve()
    project_path.mkdir(parents=True, exist_ok=True)

    # Create CLAUDE.md
    claude_md = project_path / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(
            f"# {project_path.name}\n\n"
            "## Project Description\n"
            "Describe your project here.\n\n"
            "## Hot Memory\n"
            "<!-- compressed context will appear here -->\n",
            encoding="utf-8",
        )

    # Create tasks.md
    tasks_md = project_path / "tasks.md"
    if not tasks_md.exists():
        tasks_md.write_text(
            "# Tasks\n\n"
            "- [ ] **T001** Your first task `type:draft` `p1`\n",
            encoding="utf-8",
        )

    print(f"✓ Initialized Orchid project at {project_path}")
    print(f"  Edit {claude_md} to describe your project.")
    print(f"  Edit {tasks_md} to add tasks.")