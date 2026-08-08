import time
from pathlib import Path
from unittest.mock import patch

import pytest

from orchid.firecracker_runner import FirecrackerPool, FirecrackerRunner, _WarmVM
from orchid.worker_protocol import TaskContext, WorkerResult


@pytest.fixture
def sample_ctx(tmp_path: Path) -> TaskContext:
    return TaskContext(
        task_id="FCP001",
        task_description="echo hi",
        session_context="test-session",
        agent_type="developer",
        model_key="local",
        project_dir=str(tmp_path),
        injection_queue_path=str(tmp_path / "queue.json"),
    )


def _fake_boot_vm(work_dir: Path, timeout_s):
    """Stand-in for FirecrackerRunner._boot_vm: no real Firecracker process."""
    work_dir.mkdir(parents=True, exist_ok=True)
    return object(), work_dir / "vsock.sock"


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_start_prewarms_configured_pool_size() -> None:
    runner = FirecrackerRunner()
    with patch.object(FirecrackerRunner, "is_available", return_value=True), \
         patch.object(FirecrackerRunner, "_boot_vm", side_effect=_fake_boot_vm), \
         patch.object(FirecrackerRunner, "_shutdown"):
        pool = FirecrackerPool(size=2, runner=runner)
        pool.start()
        assert _wait_until(lambda: pool.warm_count() == 2)


def test_start_does_nothing_when_unavailable() -> None:
    runner = FirecrackerRunner()
    with patch.object(FirecrackerRunner, "is_available", return_value=False), \
         patch.object(FirecrackerRunner, "_boot_vm") as mock_boot:
        pool = FirecrackerPool(size=2, runner=runner)
        pool.start()
        time.sleep(0.1)
        assert pool.warm_count() == 0
        mock_boot.assert_not_called()


def test_submit_uses_a_warm_vm_and_replenishes(sample_ctx: TaskContext, tmp_path: Path) -> None:
    runner = FirecrackerRunner()
    fake_response = {"stdout": "hi\n", "stderr": "", "exit_code": 0, "success": True}
    with patch.object(FirecrackerRunner, "_boot_vm", side_effect=_fake_boot_vm), \
         patch.object(FirecrackerRunner, "_send_task_over_vsock", return_value=fake_response) as mock_send, \
         patch.object(FirecrackerRunner, "_shutdown") as mock_shutdown:
        pool = FirecrackerPool(size=1, runner=runner)
        vm = _WarmVM(proc=object(), uds_path=tmp_path / "vsock.sock",
                     work_dir=tmp_path, booted_at=time.monotonic())
        pool._warm.put(vm)

        result = pool.submit(sample_ctx, timeout_s=5)

        assert isinstance(result, WorkerResult)
        assert result.success is True
        assert result.stdout == "hi\n"
        mock_send.assert_called_once_with(vm.uds_path, sample_ctx.task_description, 5)
        mock_shutdown.assert_called_once_with(vm.proc)
        # replenishment is async — the consumed VM's replacement should show up
        assert _wait_until(lambda: pool.warm_count() == 1)


def test_submit_cold_boots_a_fallback_when_pool_is_empty(sample_ctx: TaskContext) -> None:
    runner = FirecrackerRunner()
    fake_response = {"stdout": "hi\n", "stderr": "", "exit_code": 0, "success": True}
    with patch.object(FirecrackerRunner, "_boot_vm", side_effect=_fake_boot_vm), \
         patch.object(FirecrackerRunner, "_send_task_over_vsock", return_value=fake_response), \
         patch.object(FirecrackerRunner, "_shutdown"):
        pool = FirecrackerPool(size=1, runner=runner)
        # Empty queue, and a very small wait budget so the test doesn't hang
        # waiting for the (never-started) background pre-warm.
        result = pool.submit(sample_ctx, timeout_s=0.2)
        assert result.success is True


def test_submit_returns_error_when_cold_boot_fallback_also_fails(sample_ctx: TaskContext) -> None:
    runner = FirecrackerRunner()
    with patch.object(FirecrackerRunner, "_boot_vm", return_value=(None, None)):
        pool = FirecrackerPool(size=1, runner=runner)
        result = pool.submit(sample_ctx, timeout_s=0.2)
        assert result.success is False
        assert "cold-boot fallback also failed" in result.error


def test_submit_after_shutdown_returns_error(sample_ctx: TaskContext) -> None:
    pool = FirecrackerPool(size=1, runner=FirecrackerRunner())
    pool.shutdown()
    result = pool.submit(sample_ctx, timeout_s=1)
    assert result.success is False
    assert "closed" in result.error.lower()


def test_shutdown_drains_and_shuts_down_all_warm_vms(tmp_path: Path) -> None:
    runner = FirecrackerRunner()
    with patch.object(FirecrackerRunner, "_shutdown") as mock_shutdown:
        pool = FirecrackerPool(size=2, runner=runner)
        for i in range(2):
            pool._warm.put(_WarmVM(proc=object(), uds_path=tmp_path / f"v{i}.sock",
                                    work_dir=tmp_path, booted_at=time.monotonic()))
        pool.shutdown()
        assert pool.warm_count() == 0
        assert mock_shutdown.call_count == 2
