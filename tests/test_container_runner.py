import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from orchid.container_runner import ContainerRunner
from orchid.worker_protocol import TaskContext, WorkerResult


@pytest.fixture
def sample_ctx(tmp_path: Path) -> TaskContext:
    """Minimal TaskContext for testing."""
    return TaskContext(
        task_id="T001",
        task_description="test",
        session_context="test-session",
        agent_type="developer",
        model_key="local",
        project_dir=str(tmp_path),
        injection_queue_path=str(tmp_path / "queue.json"),
    )


def test_container_runner_unavailable_when_no_docker(tmp_path: Path) -> None:
    """Patch shutil.which to return None. Create ContainerRunner(). Assert is_available() is False."""
    with patch.object(shutil, "which", return_value=None):
        runner = ContainerRunner()
        assert runner.is_available() is False


def test_run_task_isolated_returns_failure_when_no_docker(tmp_path: Path) -> None:
    """When Docker is unavailable, run_task_isolated returns a failure WorkerResult."""
    with patch.object(shutil, "which", return_value=None):
        runner = ContainerRunner()
        ctx = TaskContext(
            task_id="T002",
            task_description="test",
            session_context="test-session",
            agent_type="developer",
            model_key="local",
            project_dir=str(tmp_path),
            injection_queue_path=str(tmp_path / "queue.json"),
        )
        result = runner.run_task_isolated(ctx)
        assert isinstance(result, WorkerResult)
        assert result.success is False
        assert "Docker is not available" in result.error


def test_is_available_calls_docker_info_when_docker_on_path(tmp_path: Path) -> None:
    """When docker CLI is on PATH but docker info fails, is_available() returns False."""
    with patch.object(shutil, "which", return_value="/usr/bin/docker"):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 5)):
            runner = ContainerRunner()
            assert runner.is_available() is False