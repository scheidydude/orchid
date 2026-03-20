"""Tests for filesystem and shell tools."""

from __future__ import annotations

import pytest
from orchid.errors import ToolError
from orchid.tools.filesystem import read_file, write_file, list_dir, append_file
from orchid.tools.shell import bash


def test_write_and_read(tmp_path):
    p = str(tmp_path / "test.txt")
    write_file(p, "hello world")
    assert read_file(p) == "hello world"


def test_append(tmp_path):
    p = str(tmp_path / "log.txt")
    write_file(p, "line1\n")
    append_file(p, "line2\n")
    assert read_file(p) == "line1\nline2\n"


def test_list_dir(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "subdir").mkdir()
    result = list_dir(str(tmp_path))
    assert "a.txt" in result
    assert "subdir/" in result


def test_bash_echo():
    result = bash("echo hello")
    assert "hello" in result


def test_bash_exit_code():
    result = bash("exit 1")
    assert "exit code: 1" in result


def test_bash_blocked():
    with pytest.raises(ToolError, match="Blocked"):
        bash("rm -rf /")


@pytest.mark.parametrize("cmd", [
    "bash -c 'rm -rf /'",          # rm -rf / inside bash -c
    "$(rm -rf /tmp/../)",           # command substitution wrapping rm -rf /
    ":(){:|:&};:",                  # fork bomb
    "echo test > /dev/sda",        # block device write
    "dd if=/dev/urandom of=/dev/sdb",  # raw disk write
    "mkfs.ext4 /dev/sda1",         # filesystem format (mkfs word boundary)
    "sudo shutdown now",            # shutdown
    "sudo reboot",                  # reboot
])
def test_bash_blocked_obfuscated(cmd):
    """Blocklist patterns should catch destructive commands in various forms."""
    with pytest.raises(ToolError, match="Blocked"):
        bash(cmd)
