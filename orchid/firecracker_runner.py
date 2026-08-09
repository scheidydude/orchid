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

P08 Phase 6: checkpoint/resume. The guest handler also writes its result
to /tmp/task_result.json, in addition to the vsock response -- Phase
4/5 already proved that a checkpoint/restore cycle severs whatever
vsock connection was open when it happened, so the *connection* can
never be the durable record of a task's outcome. The file can be read
back over a *fresh* connection after a restore, for any task, not just
ones whose own command happens to redirect its own output somewhere.
See firecracker_checkpoint.py for the checkpoint metadata store and
FirecrackerRunner.checkpoint_task()/the checkpoint-aware branch in
run_task_isolated() for how this is actually used.
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
from orchid import firecracker_snapshot as _snapshot
from orchid.firecracker_checkpoint import FirecrackerCheckpoint, FirecrackerCheckpointStore
from orchid.worker_protocol import TaskContext, WorkerResult

logger = logging.getLogger(__name__)

# Live VMs currently executing a task, keyed by task_id -- lets an
# external caller (FirecrackerRunner.checkpoint_task(), P08 Phase 6)
# find and pause/snapshot/kill a VM whose run_task_isolated() call is
# still blocked waiting for a vsock response, in a different thread.
_live_vms: dict[str, "_BootedVM"] = {}
_live_vms_lock = threading.Lock()

READY_MARKER = "job control turned off"
VSOCK_PORT = 5252
VSOCK_GUEST_CID = 3

# Runs once per vsock connection (socat's `fork` spawns a fresh interpreter
# per client). Reads exactly one JSON line describing the command to run,
# writes exactly one JSON line back with the result.
TASK_RESULT_FILE = "/tmp/task_result.json"

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
with open("/tmp/task_result.json", "w") as f:
    json.dump(result, f)
sys.stdout.write(json.dumps(result) + "\\n")
sys.stdout.flush()
"""


class FirecrackerRunnerError(Exception):
    pass


@dataclasses.dataclass
class _BootedVM:
    proc: subprocess.Popen
    uds_path: Path      # vsock UDS, for task request/response
    api_sock: Path       # Firecracker's own management API (pause/snapshot/resume)


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

        # P08 Phase 6: a prior run of this exact task_id may have been
        # checkpointed (see checkpoint_task() below) -- resume from it
        # instead of booting fresh. This is the entire "resume" trigger:
        # whatever re-attempts a task_id (a retry, a re-queue) gets this
        # transparently, no separate API needed.
        checkpoint = FirecrackerCheckpointStore().load_for_task(ctx.task_id)
        if checkpoint is not None:
            return self._resume_from_checkpoint(checkpoint, timeout_s)

        t0 = time.monotonic()
        work_dir = Path(f"/tmp/orchid-fc-{ctx.task_id}-{uuid.uuid4().hex[:8]}")
        work_dir.mkdir(parents=True, exist_ok=True)

        vm: _BootedVM | None = None
        checkpointed = False
        try:
            vm = self._boot_vm(work_dir, timeout_s)
            if vm is None:
                return WorkerResult(
                    task_id=ctx.task_id, success=False,
                    error="Firecracker microVM did not reach a ready guest shell in time",
                )

            with _live_vms_lock:
                _live_vms[ctx.task_id] = vm

            response = self._send_task_over_vsock(
                vm.uds_path, ctx.task_description, timeout_s
            )
            duration_s = time.monotonic() - t0

            if response is None and FirecrackerCheckpointStore().has_checkpoint(ctx.task_id):
                # checkpoint_task() ran concurrently, killed the VM (which
                # is exactly what made the vsock read above return None),
                # and already wrote the checkpoint record before doing so.
                checkpointed = True
                return WorkerResult(
                    task_id=ctx.task_id, success=False,
                    error="Task checkpointed mid-execution",
                    duration_s=duration_s, checkpoint_id=ctx.task_id,
                )

            return self._worker_result_from_response(ctx.task_id, response, duration_s)
        finally:
            with _live_vms_lock:
                _live_vms.pop(ctx.task_id, None)
            if not checkpointed:
                self._shutdown(vm.proc if vm else None)
                shutil.rmtree(work_dir, ignore_errors=True)
            # else: checkpoint_task() already killed the VM and the
            # work_dir must survive -- it's exactly what the checkpoint
            # references (see firecracker_checkpoint.py).

    def checkpoint_task(self, task_id: str) -> bool:
        """Pause, snapshot, and kill a live task's VM (P08 Phase 6).

        Callable from any thread while the task's own run_task_isolated()
        call is still blocked in another thread waiting for a vsock
        response -- killing the VM here is what unblocks it (Phase 4/5
        already proved a killed connection surfaces as a clean EOF on the
        reading side). Returns False if the task isn't a live Firecracker
        VM (already finished, never started, or run by a different
        backend) -- checked by the caller before falling back to another
        suspend mechanism.
        """
        with _live_vms_lock:
            vm = _live_vms.get(task_id)
        if vm is None:
            return False

        if not _snapshot.pause_vm(vm.api_sock):
            logger.warning("checkpoint_task(%s): pause failed", task_id)
            return False

        work_dir = vm.uds_path.parent
        snapshot_path = work_dir / "snapshot_file"
        mem_path = work_dir / "mem_file"
        if not _snapshot.create_snapshot(vm.api_sock, snapshot_path, mem_path):
            logger.warning("checkpoint_task(%s): snapshot failed", task_id)
            _snapshot.resume_vm(vm.api_sock)
            return False

        # Write the checkpoint record BEFORE killing -- run_task_isolated()'s
        # waiting thread only unblocks once the kill actually lands, so by
        # then this is guaranteed to already be on disk (no race).
        checkpoint = FirecrackerCheckpoint(
            task_id=task_id,
            work_dir=str(work_dir),
            snapshot_path=str(snapshot_path),
            mem_file_path=str(mem_path),
            rootfs_path=str(work_dir / "rootfs.ext4"),
            vsock_uds_path=str(vm.uds_path),
        )
        FirecrackerCheckpointStore().save(checkpoint)

        vm.proc.kill()
        try:
            vm.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        logger.info("checkpoint_task(%s): checkpointed and killed", task_id)
        return True

    def resume_task(self, task_id: str, timeout_s: float | None = None) -> WorkerResult | None:
        """Direct resume entry point for orchid.runner.BackgroundRunner
        (P08 Phase 6) -- unlike run_task_isolated(), takes just a task_id,
        no TaskContext, since _resume_from_checkpoint() never needs
        anything from the original context beyond the task_id already
        recorded in the checkpoint itself. Returns None if no checkpoint
        exists for task_id (caller falls back to another resume path).
        """
        checkpoint = FirecrackerCheckpointStore().load_for_task(task_id)
        if checkpoint is None:
            return None
        return self._resume_from_checkpoint(checkpoint, timeout_s)

    def _resume_from_checkpoint(
        self, checkpoint: FirecrackerCheckpoint, timeout_s: float | None
    ) -> WorkerResult:
        t0 = time.monotonic()
        work_dir = Path(checkpoint.work_dir)
        new_api_sock = work_dir / f"api-restored-{uuid.uuid4().hex[:8]}.sock"
        new_proc = self._spawn_bare_process(new_api_sock)

        self._wait_for_file(new_api_sock, timeout_s=5.0)
        loaded = self._retry_load_snapshot(new_api_sock, checkpoint, timeout_s=5.0)

        if not loaded:
            self._shutdown(new_proc)
            shutil.rmtree(work_dir, ignore_errors=True)
            FirecrackerCheckpointStore().delete(checkpoint.task_id)
            return WorkerResult(
                task_id=checkpoint.task_id, success=False,
                error="Failed to load Firecracker checkpoint into a new process",
            )

        response = self._read_task_result_file(
            Path(checkpoint.vsock_uds_path), timeout_s or 30.0
        )
        duration_s = time.monotonic() - t0
        result = self._worker_result_from_response(checkpoint.task_id, response, duration_s)

        self._shutdown(new_proc)
        shutil.rmtree(work_dir, ignore_errors=True)
        FirecrackerCheckpointStore().delete(checkpoint.task_id)
        return result

    def _read_task_result_file(self, uds_path: Path, timeout_s: float) -> dict | None:
        """Fresh vsock connection, poll TASK_RESULT_FILE until it exists
        (the restored task may still be finishing) or timeout_s elapses."""
        deadline = time.monotonic() + timeout_s
        poll_iterations = max(1, int(timeout_s / 0.1))
        cmd = (
            f"for i in $(seq 1 {poll_iterations}); do "
            f"[ -f {TASK_RESULT_FILE} ] && break; sleep 0.1; done; cat {TASK_RESULT_FILE}"
        )
        while time.monotonic() < deadline:
            response = self._send_task_over_vsock(uds_path, cmd, timeout_s)
            if response is None:
                time.sleep(0.2)
                continue
            stdout = response.get("stdout", "").strip()
            if not stdout:
                return None
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return None
        return None

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
    ) -> _BootedVM | None:
        """Boot a microVM and bootstrap its guest vsock listener.

        Shared by run_task_isolated() (boot-use-discard), FirecrackerPool
        (pre-boot many, hand out warm ones on demand), and the snapshot
        path (P08 Phase 4/5, needs the returned api_sock to pause/
        snapshot/resume the VM) -- the whole point of Phase 3's pool was
        to move this exact call off the request path, so it must not
        embed any per-task or per-feature assumptions.
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
            return None

        self._start_guest_listener(proc)
        return _BootedVM(proc=proc, uds_path=uds_path, api_sock=api_sock)

    def _spawn_bare_process(self, api_sock: Path) -> subprocess.Popen:
        """Start a Firecracker process with no --config-file (P08 Phase 5).

        Firecracker's own docs are explicit: a snapshot-load target
        process must be pristine -- no boot-source/drives/machine-config
        applied before the PUT /snapshot/load call. This is the restore
        counterpart to _boot_vm(), which always boots fresh via a config
        file; the two are not interchangeable.
        """
        proc = subprocess.Popen(
            [str(self._bin_path()), "--api-sock", str(api_sock), "--no-seccomp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        os.set_blocking(proc.stdout.fileno(), False)
        return proc

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

    @staticmethod
    def _wait_for_file(path: Path, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if path.exists():
                return True
            time.sleep(0.02)
        return path.exists()

    @staticmethod
    def _retry_load_snapshot(
        api_sock: Path, checkpoint: FirecrackerCheckpoint, timeout_s: float
    ) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if _snapshot.load_snapshot(
                api_sock, Path(checkpoint.snapshot_path), Path(checkpoint.mem_file_path),
                Path(checkpoint.vsock_uds_path), resume_vm=True,
            ):
                return True
            time.sleep(0.1)
        return False

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
    api_sock: Path
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
        booted = self._runner._boot_vm(work_dir, timeout_s)
        if booted is None:
            shutil.rmtree(work_dir, ignore_errors=True)
            return None
        return _WarmVM(proc=booted.proc, uds_path=booted.uds_path, api_sock=booted.api_sock,
                        work_dir=work_dir, booted_at=time.monotonic())

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
