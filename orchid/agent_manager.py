"""Per-project agent loop manager with APScheduler cron support.

Architecture (D0034, D0035):
- AgentManager tracks per-project run state
- auto_run=true triggers immediate agent loop on start()
- auto_run_schedule with cron expressions uses APScheduler BackgroundScheduler
- Each run executes in a daemon thread (not a process — web server stays single-process)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProjectConfig:
    """Minimal project configuration for the agent manager."""

    project_id: str
    path: str
    auto_run: bool = False
    auto_run_schedule: list[str] = field(default_factory=list)
    auto_run_code_model: str = "auto"


@dataclass
class _ProjectState:
    running: bool = False
    last_run: datetime | None = None
    next_run: datetime | None = None
    error: str | None = None


class AgentManager:
    """Manages per-project agent loops and APScheduler cron scheduling.

    Usage:
        configs = [ProjectConfig("myproj", "/path/to/proj", auto_run=True)]
        mgr = AgentManager(configs)
        mgr.start()
        # ... later ...
        mgr.stop()
    """

    def __init__(self, projects: list[ProjectConfig]) -> None:
        self.projects: dict[str, ProjectConfig] = {p.project_id: p for p in projects}
        self._states: dict[str, _ProjectState] = {
            p.project_id: _ProjectState() for p in projects
        }
        self._lock = threading.RLock()
        self._scheduler: Any | None = None

    def start(self) -> None:
        """Start agent loops for auto_run projects and register cron schedules."""
        scheduled = [
            p for p in self.projects.values()
            if p.auto_run and p.auto_run_schedule
        ]
        if scheduled:
            self._start_scheduler(scheduled)

        # Trigger immediate runs for auto_run projects without a cron schedule
        for proj in self.projects.values():
            if proj.auto_run and not proj.auto_run_schedule:
                self.trigger(proj.project_id)

    def stop(self) -> None:
        """Graceful shutdown — stop scheduler (currently running threads complete naturally)."""
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None
        logger.info("AgentManager stopped")

    def add_project(self, config: ProjectConfig) -> None:
        """Register a newly discovered project."""
        with self._lock:
            self.projects[config.project_id] = config
            self._states[config.project_id] = _ProjectState()

        # If scheduler is running and project has a schedule, add its jobs
        if config.auto_run and config.auto_run_schedule and self._scheduler is not None:
            self._add_scheduler_jobs([config])

    def remove_project(self, project_id: str) -> None:
        """Unregister a removed project."""
        with self._lock:
            self.projects.pop(project_id, None)
            self._states.pop(project_id, None)

    def trigger(self, project_id: str) -> bool:
        """Manually trigger an agent run for a project.

        Returns False if project is unknown or already running.
        """
        with self._lock:
            if project_id not in self.projects:
                logger.warning("trigger: unknown project %s", project_id)
                return False
            state = self._states[project_id]
            if state.running:
                logger.info("trigger: project %s already running, skipping", project_id)
                return False
            state.running = True
            state.error = None

        proj = self.projects[project_id]
        thread = threading.Thread(
            target=self._run_project,
            args=(proj,),
            name=f"orchid-agent-{project_id}",
            daemon=True,
        )
        thread.start()
        logger.info("Triggered agent run for project: %s", project_id)
        return True

    def status(self, project_id: str) -> dict[str, Any]:
        """Return status dict for a project."""
        with self._lock:
            if project_id not in self._states:
                return {"error": "unknown project"}
            state = self._states[project_id]
            return {
                "running": state.running,
                "last_run": state.last_run.isoformat() if state.last_run else None,
                "next_run": state.next_run.isoformat() if state.next_run else None,
                "error": state.error,
            }

    def all_status(self) -> dict[str, dict[str, Any]]:
        """Return status for all managed projects."""
        return {pid: self.status(pid) for pid in list(self.projects)}

    def _run_project(self, proj: ProjectConfig) -> None:
        """Execute one auto run for a project (runs in daemon thread)."""
        from orchid.orchestrator import Orchestrator
        from orchid.session import Session

        logger.info("AgentManager: starting run for %s at %s", proj.project_id, proj.path)
        try:
            session = Session(project_dir=proj.path)
            session.load()
            code_model = proj.auto_run_code_model if proj.auto_run_code_model != "auto" else None
            orch = Orchestrator(session, cli_model_override=code_model)
            orch.run_loop(max_tasks=50)
            session.close(summary="Auto run complete.")
        except Exception as exc:
            logger.exception("AgentManager: run failed for %s: %s", proj.project_id, exc)
            with self._lock:
                self._states[proj.project_id].error = str(exc)
        else:
            logger.info("AgentManager: run complete for %s", proj.project_id)
        finally:
            with self._lock:
                state = self._states[proj.project_id]
                state.running = False
                state.last_run = datetime.now(UTC)

    def _start_scheduler(self, projects: list[ProjectConfig]) -> None:
        """Start APScheduler BackgroundScheduler with cron jobs for the given projects."""
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            logger.warning(
                "apscheduler not installed; cron scheduling disabled. "
                "Run: uv pip install 'apscheduler>=3.10.0'"
            )
            return

        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass

        self._scheduler = BackgroundScheduler()
        self._add_scheduler_jobs(projects)
        self._scheduler.start()
        logger.info("APScheduler started with %d project(s)", len(projects))

    def _add_scheduler_jobs(self, projects: list[ProjectConfig]) -> None:
        """Add cron jobs for projects to an already-initialized scheduler."""
        if self._scheduler is None:
            return
        for proj in projects:
            for cron_expr in proj.auto_run_schedule:
                parts = cron_expr.strip().split()
                if len(parts) != 5:
                    logger.warning(
                        "Invalid cron expression for %s: %r (expected 5 fields)",
                        proj.project_id, cron_expr,
                    )
                    continue
                minute, hour, day, month, day_of_week = parts
                job_id = f"{proj.project_id}_{cron_expr.replace(' ', '_')}"
                try:
                    self._scheduler.add_job(
                        self.trigger,
                        "cron",
                        args=[proj.project_id],
                        minute=minute,
                        hour=hour,
                        day=day,
                        month=month,
                        day_of_week=day_of_week,
                        id=job_id,
                        replace_existing=True,
                    )
                    logger.info("Scheduled %s with cron: %s", proj.project_id, cron_expr)
                except Exception as exc:
                    logger.warning("Failed to schedule %s: %s", proj.project_id, exc)
