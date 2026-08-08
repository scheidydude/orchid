import json
import logging
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from orchid import config as cfg
from orchid import sandbox_egress
from orchid.worker_protocol import TaskContext, WorkerResult

logger = logging.getLogger(__name__)


class ContainerRunnerError(Exception):
    pass


class ContainerRunner:
    """Run a task inside a Docker container.

    If Docker is not available the runner falls back gracefully
    (returns a failure result instead of raising).
    """

    DOCKER_IMAGE: str = "python:3.12-slim"
    IMAGE: str = DOCKER_IMAGE  # backward compat alias
    WORKDIR: str = "/orchid"

    def __init__(self, image: str | None = None) -> None:
        self.image = image or self.IMAGE

    # -- public API ---------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if Docker is available on this host."""
        return self._docker_available()

    def run_task_isolated(
        self,
        ctx: TaskContext,
        stream_callback: Any | None = None,
        timeout_s: float | None = None,
    ) -> WorkerResult:
        """Run *ctx* inside a short-lived container.

        Returns a :class:`WorkerResult`.  If Docker is unavailable a
        failure result is returned immediately.
        """
        if not self._docker_available():
            logger.warning("Docker unavailable – skipping container execution")
            return WorkerResult(
                task_id=ctx.task_id,
                success=False,
                error="Docker is not available",
            )

        # Build the container command.
        # We copy the project into the container, then run the worker
        # subprocess module inside it.
        tmp_dir = self._prepare_project(ctx)

        try:
            proc = subprocess.Popen(
                self._build_docker_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            proc.stdin.write(ctx.to_json() + "\n")
            proc.stdin.flush()
            proc.stdin.close()

            # Drain stderr concurrently so a chatty child can't fill the pipe
            # buffer and deadlock while we're still reading stdout below.
            stderr_chunks: list[str] = []
            stderr_thread = threading.Thread(
                target=lambda: stderr_chunks.append(proc.stderr.read()),
                daemon=True,
            )
            stderr_thread.start()

            worker_result: WorkerResult | None = None
            stdout_lines: list[str] = []

            for line in proc.stdout:
                stdout_lines.append(line)
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data: dict[str, Any] = json.loads(stripped)
                except json.JSONDecodeError:
                    continue

                if "success" in data:
                    worker_result = WorkerResult(**data)
                elif stream_callback is not None:
                    stream_callback(data)

            try:
                proc.wait(timeout=int(timeout_s) if timeout_s else None)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()  # reap now that it's killed — avoid a zombie and get a real returncode
                worker_result = WorkerResult(
                    task_id=ctx.task_id,
                    success=False,
                    error=f"Worker timed out after {timeout_s}s",
                )

            stderr_thread.join(timeout=5)

            if worker_result is None:
                worker_result = WorkerResult(
                    task_id=ctx.task_id,
                    success=False,
                    error="Worker exited without result",
                )

            worker_result.exit_code = proc.returncode
            worker_result.stdout = "".join(stdout_lines)
            worker_result.stderr = "".join(stderr_chunks)
            return worker_result

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # -- internals ----------------------------------------------------------

    def _build_docker_command(self) -> list[str]:
        """Build the `docker run` argv, applying isolation.* config (P07)."""
        cmd = ["docker", "run", "--rm", "-i", "-w", self.WORKDIR]

        runtime = cfg.get("isolation.container_runtime", "")
        if runtime:
            cmd += ["--runtime", str(runtime)]

        memory_mb = cfg.get("isolation.container_memory_mb", 0)
        if memory_mb:
            cmd += ["--memory", f"{int(memory_mb)}m"]

        cpus = cfg.get("isolation.container_cpus", 0)
        if cpus:
            cmd += ["--cpus", str(cpus)]

        allowlist = cfg.get("isolation.container_egress_allowlist", []) or []
        if allowlist:
            proxy_url = sandbox_egress.ensure_egress_proxy(list(allowlist))
            cmd += [
                "--network", sandbox_egress.INTERNAL_NETWORK,
                "-e", f"HTTP_PROXY={proxy_url}",
                "-e", f"HTTPS_PROXY={proxy_url}",
            ]
        else:
            # FR-3: default-deny. Previously no --network flag was set at
            # all, which meant the default Docker bridge (full internet
            # access) — an intentional behavior change, not a bug.
            cmd += ["--network", "none"]

        cmd += [self.image, sys.executable, "-m", "orchid.worker_subprocess"]
        return cmd

    @staticmethod
    def _docker_available() -> bool:
        """Return True if the ``docker`` CLI is on PATH and responds."""
        if shutil.which("docker") is None:
            return False
        try:
            subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5,
            )
            return True
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            return False

    def _prepare_project(self, ctx: TaskContext) -> Path:
        """Copy the project tree into a temp dir and return its path.

        The container mounts this directory at ``self.WORKDIR``.
        """
        tmp_dir = Path(ctx.project_path) if ctx.project_path else Path.cwd()
        dest = Path("/tmp/orchid-container-") / str(ctx.task_id)
        dest.mkdir(parents=True, exist_ok=True)
        # Copy the project contents into the temp dir so the worker can
        # import the same modules.
        src = Path(tmp_dir)
        if src.exists():
            for item in src.iterdir():
                if item.name in (".venv", "__pycache__", ".git"):
                    continue
                shutil.copytree(item, dest / item.name, dirs_exist_ok=True)
        return dest