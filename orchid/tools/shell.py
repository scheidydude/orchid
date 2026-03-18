"""Shell tool — sandboxed bash execution."""

from __future__ import annotations

import re
import subprocess
from orchid import config as cfg
from orchid.errors import ToolError


# Commands that are never allowed regardless of config.
# Regex patterns are matched against the full command string (case-sensitive).
_BLOCKED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"rm\s+-[rRfF]*[rR][fF]*\s+/"),   # rm -rf /, rm -Rf /home, etc.
    re.compile(r"\bmkfs\b"),                        # filesystem format
    re.compile(r"\bdd\s+if="),                      # raw disk write
    re.compile(r":\s*\(\s*\)\s*\{"),               # fork bomb :(){
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"),  # power commands
    re.compile(r">\s*/dev/sd[a-z]"),               # direct block device write
]


def bash(command: str, timeout: int | None = None) -> str:
    """
    Run a bash command and return combined stdout+stderr.

    Raises RuntimeError if the command is blocked.
    Non-zero exit codes are reported in the output, not raised.
    """
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(command):
            raise ToolError(f"Blocked command pattern: {pattern.pattern!r}")

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
