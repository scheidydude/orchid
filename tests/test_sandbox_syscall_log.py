from pathlib import Path

from orchid.sandbox_syscall_log import summarize

# Real gVisor strace.go line shapes (release-20260803.0, verified 2026-08-07
# against an actual --debug-log output). Enter ("E") and eXit ("X") lines
# both appear per syscall; only "X" lines should be counted.
_BRK_ENTER = "I0807 19:49:37.861294       1 strace.go:561] [   1:   1] python3 E brk(0x0)"
_BRK_EXIT = (
    "I0807 19:49:37.861314       1 strace.go:599] [   1:   1] python3 X brk(0x0) "
    "= 94889911021568 (0x564d47707000) (3.95µs)"
)
_OPENAT_EXIT = (
    'I0807 19:49:37.861955       1 strace.go:608] [   1:   1] python3 X openat(AT_FDCWD /, '
    "0x7ec644e16d20 /usr/local/bin/../lib/glibc-hwcaps/x86-64-v4/libpython3.12.so.1.0, "
    "O_RDONLY|O_CLOEXEC, 0o0) = -1 errno=2 (no such file or directory) (40.969µs)"
)


def test_summarize_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert summarize(tmp_path / "does-not-exist") == ""


def test_summarize_empty_dir_returns_empty(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    assert summarize(d) == ""


def test_summarize_counts_exit_lines_only_not_enter(tmp_path: Path) -> None:
    """Each syscall logs an Enter and an eXit line — only eXit should count,
    or every syscall would be double-counted."""
    d = tmp_path / "log"
    d.mkdir()
    (d / "sandbox.log").write_text("\n".join([_BRK_ENTER, _BRK_EXIT] * 3))

    summary = summarize(d)
    assert summary == "3 syscalls, top: brk(3)"


def test_summarize_counts_and_ranks_multiple_syscalls(tmp_path: Path) -> None:
    d = tmp_path / "log"
    d.mkdir()
    lines = [_BRK_EXIT] * 5 + [_OPENAT_EXIT] * 2
    (d / "sandbox.log").write_text("\n".join(lines))

    summary = summarize(d, top_n=2)
    assert summary.startswith("7 syscalls, top:")
    assert "brk(5)" in summary
    assert "openat(2)" in summary


def test_summarize_ignores_non_strace_lines(tmp_path: Path) -> None:
    """Startup/config log lines (no [pid:tid] X <name>( shape) shouldn't
    be mistaken for syscalls, even if they mention function-call-like text."""
    d = tmp_path / "log"
    d.mkdir()
    noise = (
        "I0807 19:49:37.790940       1 cli.go:275] **************** gVisor ****************\n"
        "I0807 19:49:37.853203       1 seccomp.go:67] Installing seccomp filters for 97 syscalls(default)\n"
    )
    (d / "sandbox.log").write_text(noise + _BRK_EXIT)

    summary = summarize(d)
    assert summary == "1 syscalls, top: brk(1)"
