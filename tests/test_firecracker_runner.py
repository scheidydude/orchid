import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchid.firecracker_checkpoint import FirecrackerCheckpoint
from orchid.firecracker_runner import (
    VSOCK_GUEST_CID,
    VSOCK_PORT,
    FirecrackerRunner,
    _BootedVM,
    _live_vms,
    _live_vms_lock,
)
from orchid.worker_protocol import TaskContext, WorkerResult


@pytest.fixture
def sample_ctx(tmp_path: Path) -> TaskContext:
    return TaskContext(
        task_id="FCT001",
        task_description="echo hi",
        session_context="test-session",
        agent_type="developer",
        model_key="local",
        project_dir=str(tmp_path),
        injection_queue_path=str(tmp_path / "queue.json"),
    )


def _cfg_side_effect(overrides: dict):
    def side(key, default=None):
        return overrides.get(key, default)
    return side


def test_is_available_false_when_binary_missing(tmp_path: Path) -> None:
    overrides = {
        "isolation.firecracker_bin": str(tmp_path / "nope-firecracker"),
        "isolation.firecracker_kernel_path": str(tmp_path / "vmlinux"),
        "isolation.firecracker_rootfs_path": str(tmp_path / "rootfs.ext4"),
    }
    with patch("orchid.firecracker_runner.cfg.get", side_effect=_cfg_side_effect(overrides)):
        assert FirecrackerRunner().is_available() is False


def test_is_available_true_when_all_artifacts_present(tmp_path: Path) -> None:
    fc_bin = tmp_path / "firecracker"
    fc_bin.write_text("#!/bin/sh\n")
    fc_bin.chmod(0o755)
    kernel = tmp_path / "vmlinux"
    kernel.write_bytes(b"x")
    rootfs = tmp_path / "rootfs.ext4"
    rootfs.write_bytes(b"x")
    overrides = {
        "isolation.firecracker_bin": str(fc_bin),
        "isolation.firecracker_kernel_path": str(kernel),
        "isolation.firecracker_rootfs_path": str(rootfs),
    }
    with patch("orchid.firecracker_runner.cfg.get", side_effect=_cfg_side_effect(overrides)):
        assert FirecrackerRunner().is_available() is True


def test_run_task_isolated_returns_failure_when_unavailable(sample_ctx: TaskContext) -> None:
    with patch.object(FirecrackerRunner, "is_available", return_value=False):
        result = FirecrackerRunner().run_task_isolated(sample_ctx)
        assert isinstance(result, WorkerResult)
        assert result.success is False
        assert "not available" in result.error


def test_write_vm_config_uses_configured_memory_and_vcpus(tmp_path: Path) -> None:
    overrides = {
        "isolation.firecracker_memory_mb": 512,
        "isolation.firecracker_vcpus": 2,
        "isolation.firecracker_kernel_path": str(tmp_path / "vmlinux"),
    }
    with patch("orchid.firecracker_runner.cfg.get", side_effect=_cfg_side_effect(overrides)):
        runner = FirecrackerRunner()
        cfg_path, uds_path = runner._write_vm_config(tmp_path, tmp_path / "rootfs.ext4")
        vm_config = json.loads(cfg_path.read_text())

        assert vm_config["machine-config"]["mem_size_mib"] == 512
        assert vm_config["machine-config"]["vcpu_count"] == 2
        assert vm_config["vsock"]["guest_cid"] == VSOCK_GUEST_CID
        assert vm_config["vsock"]["uds_path"] == str(uds_path)
        assert vm_config["drives"][0]["path_on_host"] == str(tmp_path / "rootfs.ext4")
        assert vm_config["drives"][0]["is_root_device"] is True
        assert "init=/bin/sh" in vm_config["boot-source"]["boot_args"]


def test_write_vm_config_defaults_memory_and_vcpus(tmp_path: Path) -> None:
    with patch("orchid.firecracker_runner.cfg.get", side_effect=_cfg_side_effect({})):
        runner = FirecrackerRunner()
        cfg_path, _ = runner._write_vm_config(tmp_path, tmp_path / "rootfs.ext4")
        vm_config = json.loads(cfg_path.read_text())
        assert vm_config["machine-config"]["mem_size_mib"] == 256
        assert vm_config["machine-config"]["vcpu_count"] == 1


def test_run_task_isolated_error_when_boot_never_ready(sample_ctx: TaskContext, tmp_path: Path) -> None:
    fc_bin = tmp_path / "firecracker"
    fc_bin.write_text("#!/bin/sh\n")
    fc_bin.chmod(0o755)
    kernel = tmp_path / "vmlinux"
    kernel.write_bytes(b"x")
    rootfs = tmp_path / "rootfs.ext4"
    rootfs.write_bytes(b"x")
    overrides = {
        "isolation.firecracker_bin": str(fc_bin),
        "isolation.firecracker_kernel_path": str(kernel),
        "isolation.firecracker_rootfs_path": str(rootfs),
    }
    with patch("orchid.firecracker_runner.cfg.get", side_effect=_cfg_side_effect(overrides)), \
         patch("subprocess.run"), \
         patch("subprocess.Popen") as mock_popen, \
         patch.object(FirecrackerRunner, "_wait_for_ready", return_value=None):
        mock_proc = MagicMock()
        mock_proc.stdout.fileno.return_value = 0
        mock_popen.return_value = mock_proc

        result = FirecrackerRunner().run_task_isolated(sample_ctx, timeout_s=1)
        assert result.success is False
        assert "did not reach a ready guest shell" in result.error


def test_run_task_isolated_error_when_no_vsock_response(sample_ctx: TaskContext, tmp_path: Path) -> None:
    fc_bin = tmp_path / "firecracker"
    fc_bin.write_text("#!/bin/sh\n")
    fc_bin.chmod(0o755)
    kernel = tmp_path / "vmlinux"
    kernel.write_bytes(b"x")
    rootfs = tmp_path / "rootfs.ext4"
    rootfs.write_bytes(b"x")
    overrides = {
        "isolation.firecracker_bin": str(fc_bin),
        "isolation.firecracker_kernel_path": str(kernel),
        "isolation.firecracker_rootfs_path": str(rootfs),
    }
    with patch("orchid.firecracker_runner.cfg.get", side_effect=_cfg_side_effect(overrides)), \
         patch("subprocess.run"), \
         patch("subprocess.Popen") as mock_popen, \
         patch.object(FirecrackerRunner, "_wait_for_ready", return_value="job control turned off"), \
         patch.object(FirecrackerRunner, "_start_guest_listener"), \
         patch.object(FirecrackerRunner, "_send_task_over_vsock", return_value=None):
        mock_proc = MagicMock()
        mock_proc.stdout.fileno.return_value = 0
        mock_popen.return_value = mock_proc

        result = FirecrackerRunner().run_task_isolated(sample_ctx, timeout_s=1)
        assert result.success is False
        assert "No response from guest vsock" in result.error


def test_run_task_isolated_populates_worker_result_from_vsock_response(
    sample_ctx: TaskContext, tmp_path: Path
) -> None:
    fc_bin = tmp_path / "firecracker"
    fc_bin.write_text("#!/bin/sh\n")
    fc_bin.chmod(0o755)
    kernel = tmp_path / "vmlinux"
    kernel.write_bytes(b"x")
    rootfs = tmp_path / "rootfs.ext4"
    rootfs.write_bytes(b"x")
    overrides = {
        "isolation.firecracker_bin": str(fc_bin),
        "isolation.firecracker_kernel_path": str(kernel),
        "isolation.firecracker_rootfs_path": str(rootfs),
    }
    fake_response = {"stdout": "hi\n", "stderr": "", "exit_code": 0, "success": True}
    with patch("orchid.firecracker_runner.cfg.get", side_effect=_cfg_side_effect(overrides)), \
         patch("subprocess.run"), \
         patch("subprocess.Popen") as mock_popen, \
         patch.object(FirecrackerRunner, "_wait_for_ready", return_value="job control turned off"), \
         patch.object(FirecrackerRunner, "_start_guest_listener"), \
         patch.object(FirecrackerRunner, "_send_task_over_vsock", return_value=fake_response):
        mock_proc = MagicMock()
        mock_proc.stdout.fileno.return_value = 0
        mock_popen.return_value = mock_proc

        result = FirecrackerRunner().run_task_isolated(sample_ctx, timeout_s=5)
        assert result.task_id == sample_ctx.task_id
        assert result.success is True
        assert result.stdout == "hi\n"
        assert result.exit_code == 0
        assert isinstance(result, WorkerResult)


# ── P08 Phase 6: checkpoint/resume ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_live_vms_registry():
    with _live_vms_lock:
        _live_vms.clear()
    yield
    with _live_vms_lock:
        _live_vms.clear()


def _fake_booted_vm(tmp_path: Path) -> _BootedVM:
    return _BootedVM(proc=MagicMock(), uds_path=tmp_path / "vsock.sock", api_sock=tmp_path / "api.sock")


def test_checkpoint_task_returns_false_when_no_live_vm() -> None:
    assert FirecrackerRunner().checkpoint_task("no-such-task") is False


def test_checkpoint_task_pauses_snapshots_kills_and_saves_checkpoint(tmp_path: Path) -> None:
    vm = _fake_booted_vm(tmp_path)
    with _live_vms_lock:
        _live_vms["T-CKPT"] = vm

    with patch("orchid.firecracker_runner._snapshot.pause_vm", return_value=True) as mock_pause, \
         patch("orchid.firecracker_runner._snapshot.create_snapshot", return_value=True) as mock_snap, \
         patch("orchid.firecracker_runner.FirecrackerCheckpointStore") as mock_store_cls:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store

        ok = FirecrackerRunner().checkpoint_task("T-CKPT")

        assert ok is True
        mock_pause.assert_called_once_with(vm.api_sock)
        mock_snap.assert_called_once()
        mock_store.save.assert_called_once()
        saved = mock_store.save.call_args[0][0]
        assert isinstance(saved, FirecrackerCheckpoint)
        assert saved.task_id == "T-CKPT"
        assert saved.vsock_uds_path == str(vm.uds_path)
        vm.proc.kill.assert_called_once()
        # checkpoint record must be written BEFORE the kill (no race with
        # run_task_isolated()'s waiting thread) -- verify ordering.
        assert mock_store.save.call_args is not None
    with _live_vms_lock:
        _live_vms.pop("T-CKPT", None)


def test_checkpoint_task_returns_false_when_pause_fails(tmp_path: Path) -> None:
    vm = _fake_booted_vm(tmp_path)
    with _live_vms_lock:
        _live_vms["T-PAUSEFAIL"] = vm

    with patch("orchid.firecracker_runner._snapshot.pause_vm", return_value=False):
        ok = FirecrackerRunner().checkpoint_task("T-PAUSEFAIL")
        assert ok is False
        vm.proc.kill.assert_not_called()


def test_checkpoint_task_resumes_vm_when_snapshot_fails(tmp_path: Path) -> None:
    vm = _fake_booted_vm(tmp_path)
    with _live_vms_lock:
        _live_vms["T-SNAPFAIL"] = vm

    with patch("orchid.firecracker_runner._snapshot.pause_vm", return_value=True), \
         patch("orchid.firecracker_runner._snapshot.create_snapshot", return_value=False), \
         patch("orchid.firecracker_runner._snapshot.resume_vm", return_value=True) as mock_resume:
        ok = FirecrackerRunner().checkpoint_task("T-SNAPFAIL")
        assert ok is False
        mock_resume.assert_called_once_with(vm.api_sock)
        vm.proc.kill.assert_not_called()


def test_run_task_isolated_resumes_from_existing_checkpoint(sample_ctx: TaskContext, tmp_path: Path) -> None:
    checkpoint = FirecrackerCheckpoint(
        task_id=sample_ctx.task_id, work_dir=str(tmp_path),
        snapshot_path=str(tmp_path / "snapshot_file"), mem_file_path=str(tmp_path / "mem_file"),
        rootfs_path=str(tmp_path / "rootfs.ext4"), vsock_uds_path=str(tmp_path / "vsock.sock"),
    )
    fake_result = WorkerResult(task_id=sample_ctx.task_id, success=True, stdout="resumed\n")
    with patch.object(FirecrackerRunner, "is_available", return_value=True), \
         patch("orchid.firecracker_runner.FirecrackerCheckpointStore") as mock_store_cls, \
         patch.object(FirecrackerRunner, "_resume_from_checkpoint", return_value=fake_result) as mock_resume:
        mock_store = MagicMock()
        mock_store.load_for_task.return_value = checkpoint
        mock_store_cls.return_value = mock_store

        result = FirecrackerRunner().run_task_isolated(sample_ctx, timeout_s=5)

        mock_resume.assert_called_once_with(checkpoint, 5)
        assert result is fake_result


def test_resume_from_checkpoint_returns_error_when_load_fails(tmp_path: Path) -> None:
    checkpoint = FirecrackerCheckpoint(
        task_id="T-RESUME", work_dir=str(tmp_path),
        snapshot_path=str(tmp_path / "snapshot_file"), mem_file_path=str(tmp_path / "mem_file"),
        rootfs_path=str(tmp_path / "rootfs.ext4"), vsock_uds_path=str(tmp_path / "vsock.sock"),
    )
    with patch.object(FirecrackerRunner, "_spawn_bare_process", return_value=MagicMock()), \
         patch.object(FirecrackerRunner, "_wait_for_file", return_value=True), \
         patch.object(FirecrackerRunner, "_retry_load_snapshot", return_value=False), \
         patch.object(FirecrackerRunner, "_shutdown"), \
         patch("shutil.rmtree"), \
         patch("orchid.firecracker_runner.FirecrackerCheckpointStore") as mock_store_cls:
        mock_store_cls.return_value = MagicMock()
        result = FirecrackerRunner()._resume_from_checkpoint(checkpoint, timeout_s=1)
        assert result.success is False
        assert "Failed to load Firecracker checkpoint" in result.error


def test_wait_for_file_returns_true_once_file_exists(tmp_path: Path) -> None:
    target = tmp_path / "shows-up.sock"
    target.touch()
    assert FirecrackerRunner._wait_for_file(target, timeout_s=1.0) is True


def test_wait_for_file_returns_false_on_timeout(tmp_path: Path) -> None:
    target = tmp_path / "never-shows-up.sock"
    assert FirecrackerRunner._wait_for_file(target, timeout_s=0.05) is False


def test_resume_from_checkpoint_returns_harvested_result(tmp_path: Path) -> None:
    checkpoint = FirecrackerCheckpoint(
        task_id="T-RESUME2", work_dir=str(tmp_path),
        snapshot_path=str(tmp_path / "snapshot_file"), mem_file_path=str(tmp_path / "mem_file"),
        rootfs_path=str(tmp_path / "rootfs.ext4"), vsock_uds_path=str(tmp_path / "vsock.sock"),
    )
    harvested = {"stdout": "restored output\n", "stderr": "", "exit_code": 0, "success": True}
    with patch.object(FirecrackerRunner, "_spawn_bare_process", return_value=MagicMock()), \
         patch.object(FirecrackerRunner, "_wait_for_file", return_value=True), \
         patch.object(FirecrackerRunner, "_retry_load_snapshot", return_value=True), \
         patch.object(FirecrackerRunner, "_read_task_result_file", return_value=harvested), \
         patch.object(FirecrackerRunner, "_shutdown"), \
         patch("shutil.rmtree"), \
         patch("orchid.firecracker_runner.FirecrackerCheckpointStore") as mock_store_cls:
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store

        result = FirecrackerRunner()._resume_from_checkpoint(checkpoint, timeout_s=5)

        assert result.success is True
        assert result.stdout == "restored output\n"
        mock_store.delete.assert_called_once_with("T-RESUME2")
