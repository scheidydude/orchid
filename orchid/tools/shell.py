"""Shell tool — sandboxed bash execution."""

from __future__ import annotations

import subprocess
from orchid import config as cfg


# Commands that are never allowed regardless of config
_BLOCKED = frozenset([
    "rm -rf /", "mkfs", "dd if=", ":(){:|:&};:",  # fork bomb pattern
    "shutdown", "reboot", "halt", "poweroff",
])


def bash(command: str, timeout: int | None = None) -> str:
    """
    Run a bash command and return combined stdout+stderr.

    Raises RuntimeError if the command is blocked.
    Non-zero exit codes are reported in the output, not raised.
    """
    for blocked in _BLOCKED:
        if blocked in command:
            raise RuntimeError(f"Blocked command pattern: {blocked!r}")

    timeout = timeout or cfg.get("agents.bash_timeout_seconds", 60)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output.strip()
    except subprocess.TimeoutExpired:
        return f"[timeout after {timeout}s]"
