import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from orchid import sandbox_egress
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


# ── P07: isolation.* config wiring into `docker run` ────────────────────────

def _cfg_side_effect(overrides: dict) -> "callable":
    def side(key, default=None):
        return overrides.get(key, default)
    return side


def test_build_docker_command_default_has_no_runtime_or_limits(tmp_path: Path) -> None:
    """With isolation.* unset: no --runtime/--memory/--cpus, but --network none by
    default (FR-3 default-deny — a deliberate behavior change, see ADR-004)."""
    with patch("orchid.container_runner.cfg.get", side_effect=_cfg_side_effect({})):
        cmd = ContainerRunner()._build_docker_command(tmp_path)
        assert "--runtime" not in cmd
        assert "--memory" not in cmd
        assert "--cpus" not in cmd
        assert cmd[:6] == ["docker", "run", "--rm", "-i", "-w", ContainerRunner.WORKDIR]
        assert "--network" in cmd
        assert cmd[cmd.index("--network") + 1] == "none"


def test_build_docker_command_with_allowlist_uses_egress_proxy(tmp_path: Path) -> None:
    """isolation.container_egress_allowlist routes through the Squid sidecar network + proxy env vars."""
    overrides = {"isolation.container_egress_allowlist": ["example.com"]}
    with patch("orchid.container_runner.cfg.get", side_effect=_cfg_side_effect(overrides)), \
         patch("orchid.container_runner.sandbox_egress.ensure_egress_proxy",
               return_value="http://orchid-sandbox-egress-proxy:3128") as mock_ensure:
        cmd = ContainerRunner()._build_docker_command(tmp_path)
        mock_ensure.assert_called_once_with(["example.com"])
        assert "--network" in cmd
        assert cmd[cmd.index("--network") + 1] == sandbox_egress.INTERNAL_NETWORK
        assert "-e" in cmd
        env_flags = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-e"]
        assert "HTTP_PROXY=http://orchid-sandbox-egress-proxy:3128" in env_flags
        assert "HTTPS_PROXY=http://orchid-sandbox-egress-proxy:3128" in env_flags


def test_build_docker_command_applies_runsc_runtime_and_limits(tmp_path: Path) -> None:
    """isolation.container_runtime/container_memory_mb/container_cpus map onto docker run flags."""
    overrides = {
        "isolation.container_runtime": "runsc",
        "isolation.container_memory_mb": 256,
        "isolation.container_cpus": 1,
    }
    with patch("orchid.container_runner.cfg.get", side_effect=_cfg_side_effect(overrides)):
        cmd = ContainerRunner()._build_docker_command(tmp_path)
        assert "--runtime" in cmd
        assert cmd[cmd.index("--runtime") + 1] == "runsc"
        assert "--memory" in cmd
        assert cmd[cmd.index("--memory") + 1] == "256m"
        assert "--cpus" in cmd
        assert cmd[cmd.index("--cpus") + 1] == "1"


def test_build_docker_command_mounts_project_and_orchid_root(tmp_path: Path) -> None:
    """P07: the container must actually be able to import orchid and see the
    project — previously no -v mount existed at all for either."""
    with patch("orchid.container_runner.cfg.get", side_effect=_cfg_side_effect({})):
        cmd = ContainerRunner()._build_docker_command(tmp_path)
        mount_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-v"]
        assert f"{tmp_path}:{ContainerRunner.WORKDIR}" in mount_args
        assert f"{ContainerRunner.ORCHID_ROOT}:{ContainerRunner.ORCHID_ROOT}:ro" in mount_args


def test_prepare_project_uses_project_dir_field(tmp_path: Path) -> None:
    """Regression test: TaskContext has no `project_path` field (that was a
    pre-existing bug — _prepare_project must read ctx.project_dir instead,
    or run_task_isolated crashes with AttributeError before Docker even runs)."""
    src = tmp_path / "src_project"
    src.mkdir()
    (src / "example.txt").write_text("hello")
    ctx = TaskContext(
        task_id="T004",
        task_description="test",
        session_context="test-session",
        agent_type="developer",
        model_key="local",
        project_dir=str(src),
        injection_queue_path=str(tmp_path / "queue.json"),
    )
    dest = ContainerRunner()._prepare_project(ctx)
    try:
        assert (dest / "example.txt").read_text() == "hello"
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def test_run_task_isolated_populates_additive_fields_on_no_docker_failure(tmp_path: Path) -> None:
    """Even the no-Docker failure path leaves the new fields at their safe defaults."""
    with patch.object(shutil, "which", return_value=None):
        runner = ContainerRunner()
        ctx = TaskContext(
            task_id="T003",
            task_description="test",
            session_context="test-session",
            agent_type="developer",
            model_key="local",
            project_dir=str(tmp_path),
            injection_queue_path=str(tmp_path / "queue.json"),
        )
        result = runner.run_task_isolated(ctx)
        assert result.exit_code is None
        assert result.stdout == ""
        assert result.stderr == ""