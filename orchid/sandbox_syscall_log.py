"""P07 Phase 5: summarize a gVisor --debug-log syscall trace.

gVisor's strace lines look like (verified against a real
release-20260803.0 log, 2026-08-07):

    I0807 19:49:37.861314   1 strace.go:599] [   1:   1] python3 X brk(0x0) = ... (3.95us)
    I0807 19:49:37.861699   1 strace.go:576] [   1:   1] python3 E mmap(...)

Each syscall is logged twice — an Enter ("E") line when it starts and
an eXit ("X") line when it returns. Only "X" lines are counted, so
each real syscall is counted once.
"""
import re
from pathlib import Path

_SYSCALL_EXIT_RE = re.compile(r"\[\s*\d+:\s*\d+\]\s+\S+\s+X\s+([a-z_][a-z0-9_]*)\(")


def summarize(log_dir: str | Path, top_n: int = 5) -> str:
    """Return a short human-readable summary of syscall counts.

    *log_dir* is a directory (as written by --debug-log ending in '/');
    all files in it are scanned. Returns "" if the directory is missing,
    empty, or contains nothing that looks like syscall trace output.
    """
    log_dir = Path(log_dir)
    if not log_dir.exists():
        return ""

    counts: dict[str, int] = {}
    total = 0
    for path in log_dir.iterdir():
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for match in _SYSCALL_EXIT_RE.finditer(text):
            name = match.group(1)
            counts[name] = counts.get(name, 0) + 1
            total += 1

    if total == 0:
        return ""

    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    top_str = ", ".join(f"{name}({count})" for name, count in top)
    return f"{total} syscalls, top: {top_str}"
