import json
import subprocess
import threading
from unittest.mock import MagicMock, patch

from orchid.subprocess_runner import SubprocessRunner
from orchid.worker_protocol import TaskContext, WorkerEvent, WorkerResult


def test_run_task_isolated_success():
    """Subprocess returns a WorkerResult with success=True."""
    ctx = TaskContext(
        task_id="T001",
        task_description="do stuff",
        session_context="session-abc",
        agent_type="base",
        model_key="local",
        project_dir="/tmp/proj",
        injection_queue_path="/tmp/queue",
    )

    event_json = WorkerEvent(type="agent_step", task_id="T001", payload={"thought": "x"}).to_json()
    result_json = WorkerResult(task_id="T001", success=True, result="done").to_json()
    mock_stdout = iter([event_json + "\n", result_json + "\n"])

    mock_proc = MagicMock()
    mock_proc.stdout = mock_stdout
    mock_proc.wait.return_value = 0
    mock_proc.stdin = MagicMock()

    with patch("orchid.subprocess_runner.subprocess.Popen", return_value=mock_proc):
        actual = SubprocessRunner().run_task_isolated(ctx, None, None)

    assert actual.success is True
    assert actual.result == "done"


def test_run_task_isolated_calls_stream_callback():
    """stream_callback is invoked with each event payload dict."""
    ctx = TaskContext(
        task_id="T001",
        task_description="do stuff",
        session_context="session-abc",
        agent_type="base",
        model_key="local",
        project_dir="/tmp/proj",
        injection_queue_path="/tmp/queue",
    )

    event_json = WorkerEvent(type="agent_step", task_id="T001", payload={"thought": "hello"}).to_json()
    result_json = WorkerResult(task_id="T001", success=True, result="done").to_json()
    mock_stdout = iter([event_json + "\n", result_json + "\n"])

    mock_proc = MagicMock()
    mock_proc.stdout = mock_stdout
    mock_proc.wait.return_value = 0
    mock_proc.stdin = MagicMock()

    events_collected: list[dict] = []

    def stream_callback(data: dict) -> None:
        events_collected.append(data)

    with patch("orchid.subprocess_runner.subprocess.Popen", return_value=mock_proc):
        SubprocessRunner().run_task_isolated(ctx, stream_callback, None)

    assert len(events_collected) == 1
    assert events_collected[0]["type"] == "agent_step"
    assert events_collected[0]["thought"] == "hello"


def test_run_task_isolated_timeout_kills_process():
    """When the subprocess times out, the process is killed and a failure result is returned."""
    ctx = TaskContext(
        task_id="T001",
        task_description="do stuff",
        session_context="session-abc",
        agent_type="base",
        model_key="local",
        project_dir="/tmp/proj",
        injection_queue_path="/tmp/queue",
    )

    mock_proc = MagicMock()
    mock_proc.stdout = iter([])  # no output before timeout
    mock_proc.stdin = MagicMock()

    def wait_side_effect(timeout=None):
        raise subprocess.TimeoutExpired(cmd="x", timeout=5)

    mock_proc.wait.side_effect = wait_side_effect

    with patch("orchid.subprocess_runner.subprocess.Popen", return_value=mock_proc):
        actual = SubprocessRunner().run_task_isolated(ctx, None, timeout_s=5)

    assert actual.success is False
    assert "timed out" in actual.error.lower()
    mock_proc.kill.assert_called_once()