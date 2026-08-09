from pathlib import Path
from unittest.mock import MagicMock, patch

from orchid import firecracker_snapshot as snap


def _mock_curl_result(status: int, body: str = "") -> MagicMock:
    result = MagicMock()
    result.stdout = f"{body}\n{status}"
    return result


def test_pause_vm_true_on_204(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_mock_curl_result(204)) as mock_run:
        assert snap.pause_vm(tmp_path / "api.sock") is True
        cmd = mock_run.call_args[0][0]
        assert "PATCH" in cmd
        assert "--unix-socket" in cmd
        assert str(tmp_path / "api.sock") in cmd


def test_pause_vm_false_on_non_204(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_mock_curl_result(400, '{"error": "bad"}')):
        assert snap.pause_vm(tmp_path / "api.sock") is False


def test_resume_vm_true_on_204(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_mock_curl_result(204)):
        assert snap.resume_vm(tmp_path / "api.sock") is True


def test_create_snapshot_sends_correct_body(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_mock_curl_result(204)) as mock_run:
        ok = snap.create_snapshot(
            tmp_path / "api.sock", tmp_path / "snap", tmp_path / "mem", snapshot_type="Full"
        )
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert "PUT" in cmd
        body_index = cmd.index("-d") + 1
        body = cmd[body_index]
        assert '"snapshot_type": "Full"' in body
        assert str(tmp_path / "snap") in body
        assert str(tmp_path / "mem") in body


def test_create_snapshot_false_on_failure(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_mock_curl_result(400, '{"error": "not paused"}')):
        assert snap.create_snapshot(tmp_path / "api.sock", tmp_path / "snap", tmp_path / "mem") is False


def test_load_snapshot_sends_correct_body(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_mock_curl_result(204)) as mock_run:
        ok = snap.load_snapshot(
            tmp_path / "api.sock", tmp_path / "snap", tmp_path / "mem",
            tmp_path / "vsock.sock", resume_vm=True,
        )
        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert "PUT" in cmd
        body_index = cmd.index("-d") + 1
        body = cmd[body_index]
        assert '"resume_vm": true' in body
        assert str(tmp_path / "snap") in body
        assert str(tmp_path / "mem") in body
        assert '"backend_type": "File"' in body


def test_load_snapshot_false_on_failure(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_mock_curl_result(400, '{"error": "bad snapshot"}')):
        assert snap.load_snapshot(
            tmp_path / "api.sock", tmp_path / "snap", tmp_path / "mem", tmp_path / "vsock.sock"
        ) is False


def test_load_snapshot_removes_stale_vsock_socket_file(tmp_path: Path) -> None:
    """Real bug found live: a killed (not gracefully shut down) original
    process leaves its vsock UDS file on disk; the new process's bind()
    fails with EADDRINUSE unless it's removed first."""
    stale_socket = tmp_path / "vsock.sock"
    stale_socket.write_text("")  # stand-in for a leftover UDS file
    assert stale_socket.exists()

    with patch("subprocess.run", return_value=_mock_curl_result(204)):
        snap.load_snapshot(tmp_path / "api.sock", tmp_path / "snap", tmp_path / "mem", stale_socket)

    assert not stale_socket.exists()


def test_load_snapshot_tolerates_no_stale_socket_file(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_mock_curl_result(204)):
        ok = snap.load_snapshot(
            tmp_path / "api.sock", tmp_path / "snap", tmp_path / "mem", tmp_path / "never-existed.sock"
        )
        assert ok is True


def test_api_request_handles_malformed_status(tmp_path: Path) -> None:
    result = MagicMock()
    result.stdout = "not-a-status-code"
    with patch("subprocess.run", return_value=result):
        status, body = snap._api_request(tmp_path / "api.sock", "GET", "/vm")
        assert status == -1
