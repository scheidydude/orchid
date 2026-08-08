"""Firecracker microVM task isolation runner (P08).

Same contract as ContainerRunner (P07): run_task_isolated(TaskContext) ->
WorkerResult. Boots a short-lived Firecracker microVM per task, using the
CI-published kernel + Ubuntu rootfs built in P08 Phase 1
(project-08-firecracker-sandbox/findings.md has the exact build steps).

Communication with the guest is vsock (ADR-004 in the portfolio repo),
not the guest's serial console — the serial console is used only as a
one-time bootstrap channel at boot, to start a vsock listener inside the
guest. The guest-side handler (written to /tmp/task_handler.py in the guest at
boot -- the base rootfs has no /opt directory, found live) executes
TaskContext.task_description as a shell command (`/bin/sh -c`) and
returns {stdout, stderr, exit_code, success} as JSON. This is a
deliberate Phase 2 scope boundary: it proves
real contract parity (same TaskContext -> WorkerResult shape as
ContainerRunner), not a full orchid worker_subprocess/agent loop running
inside the guest — that would be a much larger, ContainerRunner-Phase-4-
sized undertaking, not yet attempted.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import logging
import os
import queue
import select
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from orchid import config as cfg
from orchid.worker_protocol import TaskContext, WorkerResult

logger = logging.getLogger(__name__)

READY_MARKER = "job control turned off"
VSOCK_PORT = 5252
VSOCK_GUEST_CID = 3

# Runs once per vsock connection (socat's `fork` spawns a fresh interpreter
# per client). Reads exactly one JSON line describing the command to run,
# writes exactly one JSON line back with the result.
_GUEST_HANDLER_SRC = """\
import json, subprocess, sys
line = sys.stdin.readline()
try:
    task = json.loads(line)
    cmd = task.get("cmd", "true")
    timeout_s = task.get("timeout_s")
    proc = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=timeout_s)
    result = {"stdout": proc.stdout, "stderr": proc.stderr, "exit_code": proc.returncode, "success": proc.returncode == 0}
except subprocess.TimeoutExpired:
    result = {"stdout": "", "stderr": "timed out", "exit_code": None, "success": False}
except Exception as e:
    result = {"stdout": "", "stderr": str(e), "exit_code": None, "success": False}
sys.stdout.write(json.dumps(result) + "\\n")
sys.stdout.flush()
"""


class FirecrackerRunnerError(Exception):
    pass


class FirecrackerRunner:
    """Run a task inside a short-lived Firecracker microVM."""

    def is_available(self) -> bool:
        fc = self._bin_path()
        kernel = self._kernel_path()
        rootfs = self._rootfs_path()
        return (
            fc.exists() and os.access(fc, os.X_OK)
            and kernel.exists()
            and rootfs.exists()
        )

    def run_task_isolated(
        self,
        ctx: TaskContext,
        stream_callback: Any | None = None,
        timeout_s: float | None = None,
    ) -> WorkerResult:
        if not self.is_available():
            logger.warning("Firecracker unavailable — skipping microVM execution")
            return WorkerResult(
                task_id=ctx.task_id,
                success=False,
                error="Firecracker is not available (binary/kernel/rootfs missing)",
            )

        t0 = time.monotonic()
        work_dir = Path(f"/tmp/orchid-fc-{ctx.task_id}-{uuid.uuid4().hex[:8]}")
        work_dir.mkdir(parents=True, exist_ok=True)
        proc: subprocess.Popen | None = None

        try:
            proc, uds_path = self._boot_vm(work_dir, timeout_s)
            if proc is None:
                return WorkerResult(
                    task_id=ctx.task_id, success=False,
                    error="Firecracker microVM did not reach a ready guest shell in time",
                )

            response = self._send_task_over_vsock(
                uds_path, ctx.task_description, timeout_s
            )
            duration_s = time.monotonic() - t0
            return self._worker_result_from_response(ctx.task_id, response, duration_s)
        finally:
            self._shutdown(proc)
            shutil.rmtree(work_dir, ignore_errors=True)

    # -- internals ------------------------------------------------------

    @staticmethod
    def _worker_result_from_response(
        task_id: str, response: dict | None, duration_s: float
    ) -> WorkerResult:
        if response is None:
            return WorkerResult(
                task_id=task_id, success=False,
                error="No response from guest vsock task handler",
                duration_s=duration_s,
            )
        success = bool(response.get("success", False))
        return WorkerResult(
            task_id=task_id,
            success=success,
            error="" if success else str(response.get("stderr", "")),
            duration_s=duration_s,
            stdout=response.get("stdout", ""),
            stderr=response.get("stderr", ""),
            exit_code=response.get("exit_code"),
        )

    def _boot_vm(
        self, work_dir: Path, timeout_s: float | None
    ) -> tuple[subprocess.Popen | None, Path | None]:
        """Boot a microVM and bootstrap its guest vsock listener.

        Shared by run_task_isolated() (boot-use-discard) and
        FirecrackerPool (pre-boot many, hand out warm ones on demand) --
        the whole point of Phase 3's pool is to move this exact call off
        the request path, so it must not embed any per-task assumptions.
        """
        rootfs_copy = self._prepare_rootfs_copy(work_dir)
        cfg_path, uds_path = self._write_vm_config(work_dir, rootfs_copy)

        api_sock = work_dir / "api.sock"
        proc = subprocess.Popen(
            [str(self._bin_path()), "--api-sock", str(api_sock),
             "--config-file", str(cfg_path), "--no-seccomp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        os.set_blocking(proc.stdout.fileno(), False)

        boot_deadline = time.monotonic() + min(timeout_s or 15.0, 15.0)
        buf = self._wait_for_ready(proc, boot_deadline)
        if buf is None:
            self._shutdown(proc)
            return None, None

        self._start_guest_listener(proc)
        return proc, uds_path

    def _prepare_rootfs_copy(self, work_dir: Path) -> Path:
        """Per-task copy of the shared base rootfs image.

        Firecracker has no native copy-on-write/backing-file support for
        raw drives, and the base image is mounted read-write (the guest
        needs to write /opt/task_handler.py). Reusing the same file
        across tasks would corrupt it under any concurrency and drift
        state across sequential runs — a real sparse-file copy per task
        (~70ms measured, see findings.md) is the correct fix, and mirrors
        ContainerRunner._prepare_project()'s per-task temp copy pattern.
        """
        src = self._rootfs_path()
        dest = work_dir / "rootfs.ext4"
        subprocess.run(["cp", "--sparse=auto", str(src), str(dest)], check=True)
        return dest

    def _write_vm_config(self, work_dir: Path, rootfs_copy: Path) -> tuple[Path, Path]:
        mem_mb = int(cfg.get("isolation.firecracker_memory_mb", 256) or 256)
        vcpus = int(cfg.get("isolation.firecracker_vcpus", 1) or 1)
        uds_path = work_dir / "vsock.sock"

        vm_config = {
            "boot-source": {
                "kernel_image_path": str(self._kernel_path()),
                "boot_args": "console=ttyS0 reboot=k panic=1 pci=off quiet loglevel=0 init=/bin/sh",
            },
            "drives": [{
                "drive_id": "rootfs", "path_on_host": str(rootfs_copy),
                "is_root_device": True, "is_read_only": False,
            }],
            "machine-config": {"vcpu_count": vcpus, "mem_size_mib": mem_mb},
            "vsock": {"guest_cid": VSOCK_GUEST_CID, "uds_path": str(uds_path)},
        }
        cfg_path = work_dir / "config.json"
        cfg_path.write_text(json.dumps(vm_config))
        return cfg_path, uds_path

    def _wait_for_ready(self, proc: subprocess.Popen, deadline: float) -> str | None:
        buf = ""
        while time.monotonic() < deadline:
            r, _, _ = select.select([proc.stdout], [], [], 0.02)
            if r:
                chunk = proc.stdout.read()
                if chunk:
                    buf += chunk
            if READY_MARKER in buf:
                return buf
            if proc.poll() is not None:
                return None
        return None

    def _start_guest_listener(self, proc: subprocess.Popen) -> None:
        b64 = base64.b64encode(_GUEST_HANDLER_SRC.encode()).decode()
        bootstrap = (
            f"echo {b64} | base64 -d > /tmp/task_handler.py\n"
            f"socat VSOCK-LISTEN:{VSOCK_PORT},reuseaddr,fork SYSTEM:'python3 /tmp/task_handler.py' &\n"
        )
        try:
            proc.stdin.write(bootstrap)
            proc.stdin.flush()
        except BrokenPipeError:
            pass

    def _send_task_over_vsock(
        self, uds_path: Path, cmd: str, timeout_s: float | None
    ) -> dict | None:
        payload = json.dumps({"cmd": cmd, "timeout_s": timeout_s}).encode() + b"\n"
        connect_deadline = time.monotonic() + 5.0
        last_err: Exception | None = None

        while time.monotonic() < connect_deadline:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(timeout_s or 30.0)
                s.connect(str(uds_path))
                s.sendall(f"CONNECT {VSOCK_PORT}\n".encode())
                handshake = s.recv(64)
                if not handshake.startswith(b"OK"):
                    s.close()
                    last_err = FirecrackerRunnerError(f"vsock handshake failed: {handshake!r}")
                    time.sleep(0.1)
                    continue
                s.sendall(payload)
                data = self._read_line(s)
                s.close()
                if data is None:
                    return None
                return json.loads(data)
            except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
                last_err = e
                time.sleep(0.1)
        if last_err:
            logger.warning("vsock connect failed after retries: %s", last_err)
        return None

    @staticmethod
    def _read_line(s: socket.socket) -> str | None:
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf.decode() if buf else None

    def _shutdown(self, proc: subprocess.Popen | None) -> None:
        if proc is None:
            return
        try:
            proc.stdin.write("poweroff -f\n")
            proc.stdin.flush()
        except Exception:
            pass
        time.sleep(0.3)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    @staticmethod
    def _bin_path() -> Path:
        return Path(cfg.get("isolation.firecracker_bin", "~/.local/bin/firecracker")).expanduser()

    @staticmethod
    def _kernel_path() -> Path:
        return Path(cfg.get(
            "isolation.firecracker_kernel_path",
            "~/.local/opt/firecracker/artifacts/vmlinux-6.18.39",
        )).expanduser()

    @staticmethod
    def _rootfs_path() -> Path:
        return Path(cfg.get(
            "isolation.firecracker_rootfs_path",
            "~/.local/opt/firecracker/artifacts/ubuntu-24.04.ext4",
        )).expanduser()


# ── FirecrackerPool (P08 Phase 3) ───────────────────────────────────────────


@dataclasses.dataclass
class _WarmVM:
    proc: subprocess.Popen
    uds_path: Path
    work_dir: Path
    booted_at: float


class FirecrackerPool:
    """Pool of pre-booted, idle Firecracker microVMs.

    Each VM is one-shot: assigned to exactly one task, then discarded
    and replaced. This isn't a shortcut -- a microVM that already ran
    one task's shell command isn't a clean isolation boundary for the
    next one (matches ContainerRunner/Firecracker's own "short-lived
    per-task" model, not a reusable-worker pool like SubprocessRunner's
    WorkerPool). What the pool buys is moving _boot_vm()'s ~1.2s cost
    off the request path: pre-booted VMs sit warm and ready, so
    submit() only pays for the vsock round trip when one's available.
    """

    def __init__(self, size: int = 2, runner: FirecrackerRunner | None = None) -> None:
        self._size = max(1, size)
        self._runner = runner or FirecrackerRunner()
        self._warm: queue.Queue[_WarmVM] = queue.Queue()
        self._closed = False

    def start(self) -> None:
        """Kick off pre-warming the pool. Async -- does not block."""
        if not self._runner.is_available():
            logger.warning("FirecrackerPool: firecracker unavailable, not pre-warming")
            return
        for _ in range(self._size):
            self._spawn_warm_vm_async()

    def warm_count(self) -> int:
        return self._warm.qsize()

    def submit(
        self,
        ctx: TaskContext,
        stream_callback: Any | None = None,
        timeout_s: float | None = None,
    ) -> WorkerResult:
        if self._closed:
            return WorkerResult(task_id=ctx.task_id, success=False, error="Pool is closed")

        t0 = time.monotonic()
        wait_budget = min(timeout_s, 20.0) if timeout_s else 20.0
        try:
            vm = self._warm.get(timeout=wait_budget)
        except queue.Empty:
            # Pool exhausted (undersized, or replenishment hasn't caught
            # up with demand) -- cold-boot a fallback rather than fail
            # the task outright. Logged, not silent: a real deployment
            # would want to know its pool is too small for its load.
            logger.warning(
                "FirecrackerPool exhausted (size=%d), cold-booting a fallback for task %s",
                self._size, ctx.task_id,
            )
            vm = self._boot_one("pool-cold", timeout_s)
            if vm is None:
                return WorkerResult(
                    task_id=ctx.task_id, success=False,
                    error="Pool exhausted and cold-boot fallback also failed to reach a ready guest shell",
                )

        try:
            response = self._runner._send_task_over_vsock(
                vm.uds_path, ctx.task_description, timeout_s
            )
            duration_s = time.monotonic() - t0
            return self._runner._worker_result_from_response(ctx.task_id, response, duration_s)
        finally:
            self._runner._shutdown(vm.proc)
            shutil.rmtree(vm.work_dir, ignore_errors=True)
            if not self._closed:
                self._spawn_warm_vm_async()

    def shutdown(self) -> None:
        self._closed = True
        while True:
            try:
                vm = self._warm.get_nowait()
            except queue.Empty:
                break
            self._runner._shutdown(vm.proc)
            shutil.rmtree(vm.work_dir, ignore_errors=True)

    # -- internals ------------------------------------------------------

    def _boot_one(self, label: str, timeout_s: float | None) -> _WarmVM | None:
        work_dir = Path(f"/tmp/orchid-fc-{label}-{uuid.uuid4().hex[:8]}")
        work_dir.mkdir(parents=True, exist_ok=True)
        proc, uds_path = self._runner._boot_vm(work_dir, timeout_s)
        if proc is None:
            shutil.rmtree(work_dir, ignore_errors=True)
            return None
        return _WarmVM(proc=proc, uds_path=uds_path, work_dir=work_dir, booted_at=time.monotonic())

    def _spawn_warm_vm_async(self) -> None:
        def _boot() -> None:
            if self._closed:
                return
            vm = self._boot_one("pool", timeout_s=15.0)
            if vm is None:
                logger.warning("FirecrackerPool: pre-warm boot failed")
                return
            if self._closed:
                self._runner._shutdown(vm.proc)
                shutil.rmtree(vm.work_dir, ignore_errors=True)
                return
            self._warm.put(vm)

        threading.Thread(target=_boot, daemon=True).start()
