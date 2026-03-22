"""Shell tool — sandboxed bash execution."""

from __future__ import annotations

import re
import shlex
import subprocess

from orchid import config as cfg
from orchid.errors import ToolError

# Commands that are never allowed regardless of config or mode.
# Matched against the full command string (case-sensitive).
_BLOCKED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"rm\s+-[rRfF]*[rR][fF]*\s+/"),   # rm -rf /, rm -Rf /home, etc.
    re.compile(r"\bmkfs\b"),                        # filesystem format
    re.compile(r"\bdd\s+if="),                      # raw disk write
    re.compile(r":\s*\(\s*\)\s*\{"),               # fork bomb :(){
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"),  # power commands
    re.compile(r">\s*/dev/sd[a-z]"),               # direct block device write
]

# Default allowlist — executables the agent is expected to need.
# Extend via agents.shell_allowlist in .orchid.yaml.
_DEFAULT_ALLOWLIST: frozenset[str] = frozenset({
    # Version control
    "git",
    # Python
    "python", "python3", "pip", "pip3", "uv", "pytest", "ruff", "mypy",
    # JavaScript / Node
    "node", "npm", "npx", "yarn", "pnpm",
    # Rust / Go
    "cargo", "rustc", "go",
    # Build
    "make", "cmake",
    # File inspection (read-only)
    "cat", "ls", "find", "grep", "head", "tail", "wc", "diff", "sort", "uniq",
    "echo", "printf", "which", "type", "env", "pwd",
    # File operations
    "mkdir", "cp", "mv", "touch", "chmod",
    # Text processing
    "sed", "awk", "cut", "tr",
    # Archive
    "tar", "zip", "unzip", "gzip",
    # Network (read-only)
    "curl", "wget",
    # Ruby / PHP / Java
    "ruby", "gem", "bundle", "php", "composer", "java", "javac", "mvn", "gradle",
    # System info (read-only)
    "ps", "df", "du", "free",
})


def _first_word(command: str) -> str:
    """Extract the executable name from a shell command string."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return ""
    # Strip any leading env-var assignments (VAR=val cmd)
    for tok in tokens:
        if "=" not in tok:
            return tok.split("/")[-1]  # handle /usr/bin/python → python
    return ""


def bash(command: str, timeout: int | None = None) -> str:
    """
    Run a bash command and return combined stdout+stderr.

    Two modes (configured via agents.shell_mode):
      blocklist (default) — blocks known-dangerous patterns; all else allowed.
      allowlist           — only permits executables in agents.shell_allowlist.

    The blocklist patterns always apply regardless of mode.
    Non-zero exit codes are reported in the output, not raised.
    """
    # Blocklist always runs first
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(command):
            raise ToolError(f"Blocked command pattern: {pattern.pattern!r}")

    # Allowlist mode
    if cfg.get("agents.shell_mode", "blocklist") == "allowlist":
        allowed = frozenset(cfg.get("agents.shell_allowlist", list(_DEFAULT_ALLOWLIST)))
        exe = _first_word(command)
        if exe not in allowed:
            raise ToolError(
                f"Command not in allowlist: {exe!r}. "
                f"Add it to agents.shell_allowlist in .orchid.yaml to permit it."
            )

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
