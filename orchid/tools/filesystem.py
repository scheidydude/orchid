"""Filesystem tools: read_file, write_file, list_dir, append_file."""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchid.locks import get_file_lock_registry
from orchid.hooks.audit import log_file_write as _audit_file_write


def read_file(path: str) -> str:
    """Return file contents as a string. Raises FileNotFoundError if missing."""
    return Path(path).read_text(encoding="utf-8")


def write_file(path: str, content: str) -> str:
    """Write content to path, creating parent dirs as needed. Runs syntax check after write."""
    registry = get_file_lock_registry()
    registry.acquire(path)
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        msg = f"Wrote {len(content)} bytes to {path}"

        suffix = p.suffix.lower()
        if suffix == ".py":
            try:
                result = subprocess.run(
                    ["python3", "-m", "py_compile", str(p)],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    msg += "\nPython syntax: OK"
                else:
                    msg += f"\nPython syntax ERROR: {result.stderr.strip()}"
            except Exception as e:
                msg += f"\nPython syntax check failed: {e}"
        elif suffix == ".js":
            try:
                result = subprocess.run(
                    ["node", "--check", str(p)],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    msg += "\nJS syntax: OK"
                else:
                    err = (result.stderr or result.stdout).strip()
                    msg += f"\nJS syntax ERROR: {err}"
            except Exception as e:
                msg += f"\nJS syntax check failed: {e}"

        try:
            _audit_file_write(task_id="", path=path, bytes_written=len(content.encode()), operation="write")
        except Exception:
            pass
        return msg
    finally:
        registry.release(path)


def append_file(path: str, content: str) -> str:
    """Append content to path."""
    registry = get_file_lock_registry()
    registry.acquire(path)
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(content)
        try:
            _audit_file_write(task_id="", path=path, bytes_written=len(content.encode()), operation="append")
        except Exception:
            pass
        return f"Appended {len(content)} bytes to {path}"
    finally:
        registry.release(path)


def list_dir(path: str = ".") -> str:
    """Return a formatted directory listing."""
    p = Path(path)
    if not p.exists():
        return f"Path does not exist: {path}"
    entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
    lines = []
    for entry in entries:
        if entry.is_dir():
            lines.append(f"  {entry.name}/")
        else:
            size = entry.stat().st_size
            lines.append(f"  {entry.name}  ({size} bytes)")
    return f"{path}/\n" + "\n".join(lines) if lines else f"{path}/ (empty)"


def file_exists(path: str) -> bool:
    return Path(path).exists()