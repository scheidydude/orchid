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

    # Repo root, e.g. /home/dave/LocalAI/orchid — mounted read-only so the
    # container can import `orchid` via the same editable install the host
    # uses (see sys.executable below). Fixes a pre-existing gap: nothing
    # previously made the orchid package importable inside the container
    # at all.
    ORCHID_ROOT: Path = Path(__file__).resolve().parent.parent

    # P07 Phase 5: per-task gVisor syscall-trace logs land here, one
    # directory per task_id, so they're directly retrievable by task ID.
    SYSCALL_LOG_ROOT: Path = Path.home() / ".orchid" / "sandbox_syscall_logs"

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
                self._build_docker_command(tmp_dir, ctx.task_id, ctx.tenant_id),
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

            log_dir = self._syscall_log_dir(ctx.task_id)
            if log_dir.exists() and any(log_dir.iterdir()):
                worker_result.syscall_log_path = str(log_dir)

            return worker_result

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # -- internals ----------------------------------------------------------

    def _build_docker_command(
        self, project_dir: Path, task_id: str, tenant_id: str = ""
    ) -> list[str]:
        """Build the `docker run` argv, applying isolation.* config (P07).

        Mounts *project_dir* (the copy `_prepare_project` made) at
        `self.WORKDIR`, plus whatever `sys.executable` (this process's own
        venv interpreter) needs to actually run inside the container —
        previously nothing was mounted at all, so `orchid.worker_subprocess`
        could never be imported regardless of image or runtime.
        """
        cmd = ["docker", "run", "--rm", "-i", "-w", self.WORKDIR]
        cmd += ["-v", f"{project_dir}:{self.WORKDIR}"]
        cmd += ["-v", f"{self.ORCHID_ROOT}:{self.ORCHID_ROOT}:ro"]
        for extra in self._venv_extra_mount_dirs():
            cmd += ["-v", f"{extra}:{extra}:ro"]

        runtime = cfg.get("isolation.container_runtime", "")
        if runtime:
            cmd += ["--runtime", str(runtime)]

        # P07 Phase 6: a tenant-specific quota (isolation.tenant_quotas[tenant_id])
        # overrides the global container_memory_mb/container_cpus for that one
        # task, so two concurrent tenants can carry independently enforced
        # caps. Unknown/empty tenant_id, or a tenant with no quota entry,
        # falls back to the existing global keys unchanged.
        tenant_quota = {}
        if tenant_id:
            tenant_quota = (cfg.get("isolation.tenant_quotas", {}) or {}).get(tenant_id) or {}

        memory_mb = tenant_quota.get("memory_mb", cfg.get("isolation.container_memory_mb", 0))
        if memory_mb:
            cmd += ["--memory", f"{int(memory_mb)}m"]

        cpus = tenant_quota.get("cpus", cfg.get("isolation.container_cpus", 0))
        if cpus:
            cmd += ["--cpus", str(cpus)]

        # FR-5: per-execution syscall trace, only meaningful under runsc.
        # Uses gVisor's documented per-container flag-override annotations
        # (dev.gvisor.flag.<name>=<value>) rather than a global runtime
        # flag, so tracing is opt-in per task, not host-wide. Requires
        # --allow-flag-override on the runsc Docker runtime — see
        # findings.md; without it, Docker rejects the annotation outright.
        if runtime == "runsc" and cfg.get("isolation.syscall_trace_enabled", False):
            log_dir = self._syscall_log_dir(task_id)
            log_dir.mkdir(parents=True, exist_ok=True)
            cmd += [
                "--annotation", "dev.gvisor.flag.strace=true",
                "--annotation", f"dev.gvisor.flag.debug-log={log_dir}/",
            ]

        # Explicit, opt-in only — nothing from the host environment is
        # forwarded by default. An isolated task's own LLM calls (e.g.
        # TesterAgent's ReAct loop) need *something* to reach the model
        # endpoint; this lets that be configured deliberately per-key
        # rather than leaking the whole host environment (secrets and
        # all) into the sandbox.
        for key, value in (cfg.get("isolation.container_env", {}) or {}).items():
            cmd += ["-e", f"{key}={value}"]

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

    @classmethod
    def _syscall_log_dir(cls, task_id: str) -> Path:
        """Per-task directory for gVisor's --debug-log output (FR-5)."""
        return cls.SYSCALL_LOG_ROOT / task_id

    @staticmethod
    def _venv_extra_mount_dirs() -> list[Path]:
        """Real (non-ORCHID_ROOT) directories `sys.executable` needs at
        runtime — e.g. a uv-managed standalone Python toolchain living
        outside the repo. Mounted read-only at their real host paths so
        `.venv`'s internal symlinks and `pyvenv.cfg` still resolve
        correctly inside the container. Empty if the interpreter lives
        entirely under ORCHID_ROOT (e.g. a plain venv over system Python).
        """
        resolved = Path(sys.executable).resolve()
        if str(resolved).startswith(str(ContainerRunner.ORCHID_ROOT)):
            return []
        uv_root = Path.home() / ".local" / "share" / "uv"
        return [uv_root] if uv_root.exists() else []

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
        tmp_dir = Path(ctx.project_dir) if ctx.project_dir else Path.cwd()
        dest = Path("/tmp/orchid-container-") / str(ctx.task_id)
        dest.mkdir(parents=True, exist_ok=True)
        # Copy the project contents into the temp dir so the worker can
        # import the same modules.
        src = Path(tmp_dir)
        if src.exists():
            for item in src.iterdir():
                if item.name in (".venv", "__pycache__", ".git"):
                    continue
                if item.is_dir():
                    shutil.copytree(item, dest / item.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest / item.name)
        return dest