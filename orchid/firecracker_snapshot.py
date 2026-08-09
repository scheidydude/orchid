"""Firecracker microVM snapshot support (P08 Phase 4/5).

Pause / snapshot-create / resume against a running microVM's management
API socket (the one FirecrackerRunner/FirecrackerPool already open per
VM as `--api-sock`, distinct from the vsock task channel). Implemented
over `curl --unix-socket` rather than a Python HTTP-over-UDS client --
this is a handful of one-shot admin calls, not a hot path, and curl
against a unix socket is simple and well-understood rather than hand-
rolling HTTP/1.1 framing.

API shape confirmed against Firecracker's own snapshotting docs before
use, not guessed: PATCH /vm {"state": "Paused"|"Resumed"} (204 on
success), PUT /snapshot/create {"snapshot_type", "snapshot_path",
"mem_file_path"} (204 on success, files written to disk immediately).
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class FirecrackerSnapshotError(Exception):
    pass


def _api_request(api_sock: Path, method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    cmd = [
        "curl", "-s", "-o", "-", "-w", "\n%{http_code}",
        "--unix-socket", str(api_sock),
        "-X", method, f"http://localhost{path}",
    ]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    output = result.stdout
    if "\n" in output:
        body_text, _, status_text = output.rpartition("\n")
    else:
        body_text, status_text = "", output
    try:
        status = int(status_text.strip())
    except ValueError:
        status = -1
    return status, body_text


def pause_vm(api_sock: Path) -> bool:
    status, body = _api_request(api_sock, "PATCH", "/vm", {"state": "Paused"})
    if status != 204:
        logger.warning("pause_vm failed: status=%s body=%s", status, body)
    return status == 204


def resume_vm(api_sock: Path) -> bool:
    status, body = _api_request(api_sock, "PATCH", "/vm", {"state": "Resumed"})
    if status != 204:
        logger.warning("resume_vm failed: status=%s body=%s", status, body)
    return status == 204


def create_snapshot(
    api_sock: Path,
    snapshot_path: Path,
    mem_file_path: Path,
    snapshot_type: str = "Full",
) -> bool:
    """Create a snapshot of a PAUSED microVM. Caller must pause_vm() first --
    Firecracker requires it, this doesn't do it implicitly, so a caller
    that forgets gets a real 400 from the API, not silent wrong behavior.
    """
    status, body = _api_request(api_sock, "PUT", "/snapshot/create", {
        "snapshot_type": snapshot_type,
        "snapshot_path": str(snapshot_path),
        "mem_file_path": str(mem_file_path),
    })
    if status != 204:
        logger.warning("create_snapshot failed: status=%s body=%s", status, body)
    return status == 204
