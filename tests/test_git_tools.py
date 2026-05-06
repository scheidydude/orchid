"""Tests for the Orchid git tools module.

Covers every public function in orchid.tools.git:
- git_status, git_diff, git_log, git_show
- git_branches, git_tags, git_remote
- git_stash_list, git_blame, git_check_ignore
- git_ls_files, git_diff_summary
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from orchid.errors import ToolError
from orchid.tools.git import (
    _git_cmd,
    git_status,
    git_diff,
    git_log,
    git_show,
    git_branches,
    git_tags,
    git_remote,
    git_stash_list,
    git_blame,
    git_check_ignore,
    git_ls_files,
    git_diff_summary,
)


# -- _git_cmd helpers --

class TestGitCmd:
    """Tests for the internal _git_cmd runner."""

    def test_successful_command(self):
        result = _git_cmd(["--version"])
        assert "git version" in result

    def test_non_zero_exit_code(self):
        result = _git_cmd(["--invalid-flag-that-does-not-exist-xyz"])
        assert "[exit code:" in result

    def test_timeout(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["git", "log"], timeout=30
            )
            result = _git_cmd(["log"], timeout=30)
            assert "timed out after 30s" in result

    def test_file_not_found(self):
        # Mock the subprocess path so it raises FileNotFoundError
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(ToolError, match="git is not installed"):
                _git_cmd(["status"])

    def test_cwd_argument(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="ok", stderr="", returncode=0
            )
            _git_cmd(["status"], cwd=Path("/tmp"))
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            assert call_kwargs.kwargs["cwd"] == Path("/tmp")

    def test_stderr_appended(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="stdout line\n",
                stderr="stderr line\n",
                returncode=0,
            )
            result = _git_cmd(["status"])
            assert "[stderr]" in result
            assert "stderr line" in result


# -- git_status --

class TestGitStatus:
    """Tests for git_status."""

    def test_default_status(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = "nothing to commit"
            result = git_status()
            mock_cmd.assert_called_once_with(
                ["status", "--untracked-files=all"],
                cwd=Path("."),
            )
            assert "nothing to commit" in result

    def test_short_format(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = "M file.txt"
            result = git_status(short=True)
            mock_cmd.assert_called_once_with(
                ["status", "--short", "--untracked-files=all"],
                cwd=Path("."),
            )

    def test_untracked_files_no(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_status(untracked_files="no")
            mock_cmd.assert_called_once_with(
                ["status", "--untracked-files=no"],
                cwd=Path("."),
            )

    def test_custom_path(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = "M file.txt"
            git_status(path="/some/repo")
            mock_cmd.assert_called_once_with(
                ["status", "--untracked-files=all"],
                cwd=Path("/some/repo"),
            )


# -- git_diff --

class TestGitDiff:
    """Tests for git_diff."""

    def test_default_diff(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = "diff --git a/f b/f"
            result = git_diff()
            mock_cmd.assert_called_once_with(
                ["diff"],
                cwd=Path("."),
            )
            assert "diff --git" in result

    def test_staged_diff(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_diff(staged=True)
            mock_cmd.assert_called_once_with(
                ["diff", "--cached"],
                cwd=Path("."),
            )

    def test_cached_alias(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_diff(cached=True)
            mock_cmd.assert_called_once_with(
                ["diff", "--cached"],
                cwd=Path("."),
            )

    def test_include_untracked(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_diff(include_untracked=True)
            mock_cmd.assert_called_once_with(
                ["diff", "--untracked-files"],
                cwd=Path("."),
            )

    def test_combined_flags(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_diff(staged=True, include_untracked=True)
            mock_cmd.assert_called_once_with(
                ["diff", "--cached", "--untracked-files"],
                cwd=Path("."),
            )


# -- git_log --

class TestGitLog:
    """Tests for git_log."""

    def test_default_log(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = "abc1234 feat: add feature"
            result = git_log(max_count=5)
            mock_cmd.assert_called_once_with(
                ["log", "--oneline", "-n", "5"],
                cwd=Path("."),
            )
            assert "abc1234" in result

    def test_no_oneline(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_log(oneline=False)
            mock_cmd.assert_called_once_with(
                ["log", "-n", "20"],
                cwd=Path("."),
            )

    def test_author_filter(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_log(author="Alice")
            mock_cmd.assert_called_once_with(
                ["log", "--oneline", "-n", "20", "--author", "Alice"],
                cwd=Path("."),
            )

    def test_date_range(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_log(since="1 week ago", until="2 days ago")
            mock_cmd.assert_called_once_with(
                [
                    "log",
                    "--oneline",
                    "-n",
                    "20",
                    "--since",
                    "1 week ago",
                    "--until",
                    "2 days ago",
                ],
                cwd=Path("."),
            )

    def test_file_filter(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_log(file_filter="src/main.py")
            mock_cmd.assert_called_once_with(
                [
                    "log",
                    "--oneline",
                    "-n",
                    "20",
                    "--",
                    "src/main.py",
                ],
                cwd=Path("."),
            )

    def test_custom_max_count(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_log(max_count=100)
            mock_cmd.assert_called_once_with(
                ["log", "--oneline", "-n", "100"],
                cwd=Path("."),
            )


# -- git_show --

class TestGitShow:
    """Tests for git_show."""

    def test_default_show(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = "commit abc123"
            result = git_show()
            mock_cmd.assert_called_once_with(
                ["show", "HEAD"],
                cwd=Path("."),
            )
            assert "commit abc123" in result

    def test_custom_commit(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_show(commit="def456")
            mock_cmd.assert_called_once_with(
                ["show", "def456"],
                cwd=Path("."),
            )

    def test_with_stat(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_show(stat=True)
            mock_cmd.assert_called_once_with(
                ["show", "--stat", "HEAD"],
                cwd=Path("."),
            )


# -- git_branches --

class TestGitBranches:
    """Tests for git_branches."""

    def test_local_branches(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = "* main\n  develop"
            result = git_branches()
            mock_cmd.assert_called_once_with(
                ["branch"],
                cwd=Path("."),
            )
            assert "* main" in result

    def test_remote_branches(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_branches(remote=True)
            mock_cmd.assert_called_once_with(
                ["branch", "--remotes"],
                cwd=Path("."),
            )

    def test_all_branches(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_branches(all_branches=True)
            mock_cmd.assert_called_once_with(
                ["branch", "--all"],
                cwd=Path("."),
            )


# -- git_tags --

class TestGitTags:
    """Tests for git_tags."""

    def test_default_tags(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = "v1.0.0\nv2.0.0"
            result = git_tags()
            mock_cmd.assert_called_once_with(
                ["tag"],
                cwd=Path("."),
            )
            assert "v1.0.0" in result

    def test_sorted_by_date(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_tags(sort_by_date=True)
            mock_cmd.assert_called_once_with(
                ["tag", "--sort=-creatordate"],
                cwd=Path("."),
            )


# -- git_remote --

class TestGitRemote:
    """Tests for git_remote."""

    def test_default_remote(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = "origin  https://github.com/example/repo.git"
            result = git_remote()
            mock_cmd.assert_called_once_with(
                ["remote"],
                cwd=Path("."),
            )
            assert "origin" in result

    def test_verbose_remote(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_remote(verbose=True)
            mock_cmd.assert_called_once_with(
                ["remote", "-v"],
                cwd=Path("."),
            )


# -- git_stash_list --

class TestGitStashList:
    """Tests for git_stash_list."""

    def test_default_stash_list(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = "stash@{0}: WIP on main"
            result = git_stash_list()
            mock_cmd.assert_called_once_with(
                ["stash", "list"],
                cwd=Path("."),
            )
            assert "stash@{0}" in result

    def test_with_stat(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_stash_list(show_stat=True)
            mock_cmd.assert_called_once_with(
                ["stash", "list", "--stat"],
                cwd=Path("."),
            )


# -- git_blame --

class TestGitBlame:
    """Tests for git_blame."""

    def test_blame_requires_file(self):
        with pytest.raises(ToolError, match="file_path"):
            git_blame()

    def test_default_blame(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = "abc12345 (Alice 2024-01-01 1) hello"
            result = git_blame(file_path="main.py")
            mock_cmd.assert_called_once_with(
                ["blame", "main.py"],
                cwd=Path("."),
            )
            assert "Alice" in result

    def test_blame_with_line_range(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_blame(file_path="main.py", line_range=(10, 20))
            mock_cmd.assert_called_once_with(
                ["blame", "-L", "10,20", "main.py"],
                cwd=Path("."),
            )

    def test_blame_ignore_whitespace(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_blame(file_path="main.py", ignore_whitespace=True)
            mock_cmd.assert_called_once_with(
                ["blame", "-w", "main.py"],
                cwd=Path("."),
            )

    def test_blame_combined(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_blame(
                file_path="main.py",
                line_range=(1, 5),
                ignore_whitespace=True,
            )
            mock_cmd.assert_called_once_with(
                ["blame", "-w", "-L", "1,5", "main.py"],
                cwd=Path("."),
            )

    def test_blame_custom_path(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_blame(path="/other/repo", file_path="app.js")
            mock_cmd.assert_called_once_with(
                ["blame", "app.js"],
                cwd=Path("/other/repo"),
            )


# -- git_check_ignore --

class TestGitCheckIgnore:
    """Tests for git_check_ignore.

    The function signature is git_check_ignore(path=".", *file_paths).
    The first positional arg binds to `path`, and subsequent positional
    args go into `*file_paths`. To pass file paths, you must supply
    them as additional positional args after the cwd string.
    """

    def test_check_ignore_requires_path(self):
        """No file paths at all raises ToolError."""
        with pytest.raises(ToolError, match="at least one"):
            git_check_ignore()

    def test_single_file(self):
        """One file path as the sole positional arg — binds to path,
        leaving file_paths empty, so it raises ToolError.  We must
        supply the cwd explicitly as a keyword to make the first
        positional arg a file path."""
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = ".gitignore:1:*.pyc"
            # path=".", "build.pyc"  — cwd is "." and file is "build.pyc"
            result = git_check_ignore(".", "build.pyc")
            mock_cmd.assert_called_once_with(
                ["check-ignore", "-v", "build.pyc"],
                cwd=Path("."),
            )
            assert ".gitignore" in result

    def test_multiple_files(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_check_ignore(".", "a.py", "b.pyc", "c.o")
            mock_cmd.assert_called_once_with(
                ["check-ignore", "-v", "a.py", "b.pyc", "c.o"],
                cwd=Path("."),
            )

    def test_custom_cwd(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value = ".gitignore:1:*.pyc"
            git_check_ignore("/other/repo", "build.pyc")
            mock_cmd.assert_called_once_with(
                ["check-ignore", "-v", "build.pyc"],
                cwd=Path("/other/repo"),
            )


# -- git_ls_files --

class TestGitLsFiles:
    """Tests for git_ls_files."""

    def test_default_ls_files(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value="README.md\nsrc/main.py"
            result = git_ls_files()
            mock_cmd.assert_called_once_with(
                ["ls-files"],
                cwd=Path("."),
            )
            assert "README.md" in result

    def test_cached_only(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_ls_files(cached=True)
            mock_cmd.assert_called_once_with(
                ["ls-files", "--cached"],
                cwd=Path("."),
            )

    def test_with_stage(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_ls_files(stage=3)
            mock_cmd.assert_called_once_with(
                ["ls-files", "--stage"],
                cwd=Path("."),
            )

    def test_cached_and_stage(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_ls_files(cached=True, stage=0)
            mock_cmd.assert_called_once_with(
                ["ls-files", "--cached", "--stage"],
                cwd=Path("."),
            )


# -- git_diff_summary --

class TestGitDiffSummary:
    """Tests for git_diff_summary."""

    def test_default_summary(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            mock_cmd.return_value=" 1 file changed, 10 insertions(+)"
            result = git_diff_summary()
            mock_cmd.assert_called_once_with(
                ["diff", "--stat", "--numstat", "HEAD"],
                cwd=Path("."),
            )
            assert "1 file changed" in result

    def test_two_commit_range(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_diff_summary(commit_a="abc123", commit_b="def456")
            mock_cmd.assert_called_once_with(
                ["diff", "--stat", "--numstat", "abc123..def456"],
                cwd=Path("."),
            )

    def test_custom_path(self):
        with patch("orchid.tools.git._git_cmd") as mock_cmd:
            git_diff_summary(path="/other/repo")
            mock_cmd.assert_called_once_with(
                ["diff", "--stat", "--numstat", "HEAD"],
                cwd=Path("/other/repo"),
            )