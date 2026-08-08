import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchid.firecracker_runner import VSOCK_GUEST_CID, VSOCK_PORT, FirecrackerRunner
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
