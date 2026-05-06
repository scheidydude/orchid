"""Tests for the Orchid worktree manager module.

Covers every public method in orchid.worktree.WorktreeManager:
- create, remove, list_worktrees, get_worktree_path, get_worktree_branch
- remove_all, prune, commit_worktree, diff_worktree, status_worktree
- remove_by_branch
- Internal: _cleanup_oldest, _load_existing, _worktree_branch_exists
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchid.errors import ToolError
from orchid.worktree import WorktreeInfo, WorktreeManager


# -- WorktreeInfo --

class TestWorktreeInfo:
    """Tests for the WorktreeInfo dataclass."""

    def test_to_dict(self):
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=Path("/tmp/wt-T170-developer"),
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        d = info.to_dict()
        assert d["branch"] == "wt-T170-developer"
        assert d["path"] == "/tmp/wt-T170-developer"
        assert d["task_id"] == "T170"
        assert d["status"] == "active"
        assert d["created_at"] == "2024-01-01T00:00:00+00:00"

    def test_defaults(self):
        info = WorktreeInfo(
            branch="main",
            path=Path("/tmp/main"),
        )
        assert info.created_at == ""
        assert info.task_id == ""
        assert info.status == "active"


# -- WorktreeManager --

class TestWorktreeManager:
    """Tests for WorktreeManager core operations."""

    def _make_manager(self, project_dir: Path) -> WorktreeManager:
        """Create a manager with mocked git operations."""
        return WorktreeManager(project_dir)

    def test_init_creates_worktrees_dir(self, tmp_path: Path):
        wt_dir = tmp_path / ".orchid" / "worktrees"
        assert not wt_dir.exists()
        manager = WorktreeManager(tmp_path)
        assert wt_dir.exists()
        assert manager.worktrees_dir == wt_dir

    def test_init_loads_config(self, tmp_path: Path):
        with patch("orchid.config.get") as mock_get:
            mock_get.side_effect = lambda key, default: {
                "worktree.max_worktrees": 5,
                "worktree.auto_cleanup": False,
            }.get(key, default)
            manager = WorktreeManager(tmp_path)
            assert manager.max_worktrees == 5
            assert manager._auto_cleanup is False

    def test_create_basic(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        with patch.object(manager, "_list_git_worktree_branches") as mock_list:
            mock_list.return_value = set()
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout="", stderr="", returncode=0
                )
                mock_run.return_value.stdout = ""
                mock_run.return_value.stderr = ""
                mock_run.return_value.returncode = 0
                path = manager.create("T170", "developer")
                assert path == tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
                mock_run.assert_called_once()
                call_args = mock_run.call_args
                assert call_args.args[0][:4] == ["git", "worktree", "add", "-b"]
                assert call_args.args[0][4] == "wt-T170-developer"

    def test_create_custom_branch(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="", returncode=0
            )
            path = manager.create("T170", "developer", branch_name="my-branch")
            call_args = mock_run.call_args
            assert call_args.args[0][4] == "my-branch"

    def test_create_custom_base_ref(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="", returncode=0
            )
            path = manager.create("T170", "developer", base_ref="main")
            call_args = mock_run.call_args
            assert call_args.args[0][6] == "main"

    def test_create_limit_exceeded_no_cleanup(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        manager.max_worktrees = 1
        manager._auto_cleanup = False
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="", returncode=0
            )
            manager.create("T170", "developer")
            with pytest.raises(ToolError, match="Worktree limit reached"):
                manager.create("T171", "developer")

    def test_create_limit_exceeded_with_cleanup(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        manager.max_worktrees = 1
        manager._auto_cleanup = True
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="", returncode=0
            )
            manager.create("T170", "developer")
            # Second create should auto-remove T170 and create T171
            path = manager.create("T171", "developer")
            assert path == tmp_path / ".orchid" / "worktrees" / "wt-T171-developer"

    def test_create_timeout(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        with patch.object(manager, "_list_git_worktree_branches") as mock_list:
            mock_list.return_value = set()
            with patch("orchid.worktree.subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired(
                    cmd=["git", "worktree", "add", "-b", "wt-T170-developer", str(tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"), "HEAD"],
                    timeout=30
                )
                with pytest.raises(ToolError, match="timed out"):
                    manager.create("T170", "developer")

    def test_create_git_not_found(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        with patch.object(manager, "_list_git_worktree_branches") as mock_list:
            mock_list.return_value = set()
            with patch("orchid.worktree.subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError()
                with pytest.raises(ToolError, match="git is not installed"):
                    manager.create("T170", "developer")

    def test_create_git_failure(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="fatal: a worktree already exists", returncode=128
            )
            with pytest.raises(ToolError, match="git worktree add failed"):
                manager.create("T170", "developer")

    def test_create_reuses_existing(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        # Simulate an existing worktree in the registry
        existing_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=existing_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info

        # Mock _list_git_worktree_branches so _worktree_branch_exists returns True
        with patch.object(manager, "_list_git_worktree_branches") as mock_list:
            mock_list.return_value = {"wt-T170-developer"}
            path = manager.create("T170", "developer")
            # Should reuse existing, not call subprocess
            assert path == existing_path

    def test_remove_by_task_id(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="", returncode=0
            )
            result = manager.remove("T170")
            assert "Worktree removed" in result
            assert info.status == "removed"

    def test_remove_not_found(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        result = manager.remove("T999")
        assert "worktree not found" in result

    def test_remove_by_branch_name(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="", returncode=0
            )
            result = manager.remove("T170", branch_name="wt-T170-developer")
            assert "Worktree removed" in result

    def test_remove_git_failure(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="fatal: worktree remove failed", returncode=1
            )
            with pytest.raises(ToolError, match="git worktree remove failed"):
                manager.remove("T170")

    def test_list_worktrees(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info

        # Add a removed one
        info2 = WorktreeInfo(
            branch="wt-T171-developer",
            path=wt_path / ".." / "wt-T171-developer",
            created_at="2024-01-02T00:00:00+00:00",
            task_id="T171",
            status="removed",
        )
        manager._worktrees["wt-T171-developer"] = info2

        result = manager.list_worktrees()
        assert len(result) == 1
        assert result[0]["branch"] == "wt-T170-developer"

    def test_get_worktree_path(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info

        assert manager.get_worktree_path("T170") == wt_path
        assert manager.get_worktree_path("T999") is None

    def test_get_worktree_branch(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info

        assert manager.get_worktree_branch("T170") == "wt-T170-developer"
        assert manager.get_worktree_branch("T999") is None

    def test_remove_all(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        for i in range(3):
            wt_path = tmp_path / ".orchid" / "worktrees" / f"wt-T{i:03d}-developer"
            info = WorktreeInfo(
                branch=f"wt-T{i:03d}-developer",
                path=wt_path,
                created_at=f"2024-01-0{i+1}T00:00:00+00:00",
                task_id=f"T{i:03d}",
                status="active",
            )
            manager._worktrees[f"wt-T{i:03d}-developer"] = info

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="", returncode=0
            )
            result = manager.remove_all()
            assert "Removed 3 worktree(s)" in result
            # All should be removed
            for info in manager._worktrees.values():
                assert info.status == "removed"

    def test_remove_all_empty(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        result = manager.remove_all()
        assert "No active worktrees" in result

    def test_prune_removes_orphaned(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info

        # Simulate git worktree list returning no wt-* branches
        with patch.object(manager, "_list_git_worktree_branches") as mock_list:
            mock_list.return_value = set()
            result = manager.prune()
            assert "Pruned 1 orphaned" in result
            assert info.status == "removed"

    def test_prune_no_orphans(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info

        with patch.object(manager, "_list_git_worktree_branches") as mock_list:
            mock_list.return_value = {"wt-T170-developer"}
            result = manager.prune()
            assert "No orphaned" in result


# -- Git operations on worktrees --

class TestWorktreeGitOps:
    """Tests for commit_worktree, diff_worktree, status_worktree."""

    def _make_manager_with_worktree(self, tmp_path: Path, task_id: str = "T170") -> tuple[WorktreeManager, Path]:
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / f"wt-{task_id}-developer"
        info = WorktreeInfo(
            branch=f"wt-{task_id}-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id=task_id,
            status="active",
        )
        manager._worktrees[f"wt-{task_id}-developer"] = info
        return manager, wt_path

    def test_commit_worktree_success(self, tmp_path: Path):
        manager, wt_path = self._make_manager_with_worktree(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="", returncode=0
            )
            result = manager.commit_worktree("T170")
            assert "committed in" in result
            # Verify git add and git commit were called
            assert mock_run.call_count == 2

    def test_commit_worktree_no_changes(self, tmp_path: Path):
        manager, wt_path = self._make_manager_with_worktree(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="nothing to commit, working tree clean", returncode=1
            )
            result = manager.commit_worktree("T170")
            assert "no changes to commit" in result

    def test_commit_worktree_not_found(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        result = manager.commit_worktree("T999")
        assert "worktree not found" in result

    def test_commit_worktree_git_failure(self, tmp_path: Path):
        manager, wt_path = self._make_manager_with_worktree(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="error: not a git repository", returncode=1
            )
            with pytest.raises(ToolError, match="git commit failed"):
                manager.commit_worktree("T170")

    def test_diff_worktree(self, tmp_path: Path):
        manager, wt_path = self._make_manager_with_worktree(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=" README.md | 5 +++--\n 1 file changed, 3 insertions(+), 2 deletions(-)",
                stderr="",
                returncode=0,
            )
            result = manager.diff_worktree("T170")
            assert "README.md" in result

    def test_diff_worktree_no_changes(self, tmp_path: Path):
        manager, wt_path = self._make_manager_with_worktree(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
            result = manager.diff_worktree("T170")
            assert "[no changes]" in result

    def test_diff_worktree_not_found(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        result = manager.diff_worktree("T999")
        assert "worktree not found" in result

    def test_status_worktree(self, tmp_path: Path):
        manager, wt_path = self._make_manager_with_worktree(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=" M README.md\n?? new_file.txt",
                stderr="",
                returncode=0,
            )
            result = manager.status_worktree("T170")
            assert "README.md" in result

    def test_status_worktree_clean(self, tmp_path: Path):
        manager, wt_path = self._make_manager_with_worktree(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
            result = manager.status_worktree("T170")
            assert "[working tree clean]" in result

    def test_status_worktree_not_found(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        result = manager.status_worktree("T999")
        assert "worktree not found" in result


# -- Internal helpers --

class TestWorktreeHelpers:
    """Tests for internal helper methods."""

    def test_find_by_task_id(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info

        found = manager._find_by_task_id("T170")
        assert found is info
        assert manager._find_by_task_id("T999") is None

    def test_find_by_task_id_ignores_removed(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="removed",
        )
        manager._worktrees["wt-T170-developer"] = info

        assert manager._find_by_task_id("T170") is None

    def test_worktree_branch_exists(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        with patch.object(manager, "_list_git_worktree_branches") as mock_list:
            mock_list.return_value = {"wt-T170-developer", "wt-T171-developer"}
            assert manager._worktree_branch_exists("wt-T170-developer") is True
            assert manager._worktree_branch_exists("wt-T999-developer") is False

    def test_cleanup_oldest_selects_correct(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path_170 = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        wt_path_171 = tmp_path / ".orchid" / "worktrees" / "wt-T171-developer"

        info_170 = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path_170,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="active",
        )
        info_171 = WorktreeInfo(
            branch="wt-T171-developer",
            path=wt_path_171,
            created_at="2024-01-02T00:00:00+00:00",
            task_id="T171",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info_170
        manager._worktrees["wt-T171-developer"] = info_171

        with patch.object(manager, "remove_by_branch") as mock_remove:
            manager._cleanup_oldest()
            # T170 should be selected as oldest
            mock_remove.assert_called_once_with("wt-T170-developer")

    def test_cleanup_oldest_empty_worktrees(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        with patch.object(manager, "remove_by_branch") as mock_remove:
            manager._cleanup_oldest()
            mock_remove.assert_not_called()

    def test_cleanup_oldest_skips_no_timestamp(self, tmp_path: Path):
        """Worktrees loaded from existing git have empty created_at and should be skipped."""
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="",  # No timestamp
            task_id="T170",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info

        with patch.object(manager, "remove_by_branch") as mock_remove:
            manager._cleanup_oldest()
            # Should not attempt to remove since no timestamp
            mock_remove.assert_not_called()

    def test_cleanup_oldest_mixed_timestamps(self, tmp_path: Path):
        """Only worktrees with timestamps should be considered for cleanup."""
        manager = WorktreeManager(tmp_path)
        wt_path_170 = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        wt_path_171 = tmp_path / ".orchid" / "worktrees" / "wt-T171-developer"

        # T170 has no timestamp (loaded from existing)
        info_170 = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path_170,
            created_at="",
            task_id="T170",
            status="active",
        )
        # T171 has a timestamp
        info_171 = WorktreeInfo(
            branch="wt-T171-developer",
            path=wt_path_171,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T171",
            status="active",
        )
        manager._worktrees["wt-T170-developer"] = info_170
        manager._worktrees["wt-T171-developer"] = info_171

        with patch.object(manager, "remove_by_branch") as mock_remove:
            manager._cleanup_oldest()
            # Only T171 should be selected (T170 has no timestamp)
            mock_remove.assert_called_once_with("wt-T171-developer")

    def test_remove_by_branch_not_found(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        result = manager.remove_by_branch("nonexistent")
        assert "worktree not found" in result

    def test_remove_by_branch_inactive(self, tmp_path: Path):
        manager = WorktreeManager(tmp_path)
        wt_path = tmp_path / ".orchid" / "worktrees" / "wt-T170-developer"
        info = WorktreeInfo(
            branch="wt-T170-developer",
            path=wt_path,
            created_at="2024-01-01T00:00:00+00:00",
            task_id="T170",
            status="removed",
        )
        manager._worktrees["wt-T170-developer"] = info

        result = manager.remove_by_branch("wt-T170-developer")
        assert "worktree not found or inactive" in result


# -- Convenience functions --

class TestConvenienceFunctions:
    """Tests for top-level convenience functions."""

    def test_create_worktree(self, tmp_path: Path):
        with patch("orchid.worktree.WorktreeManager") as MockManager:
            mock_manager = MagicMock()
            MockManager.return_value = mock_manager
            from orchid.worktree import create_worktree
            create_worktree(tmp_path, "T170")
            MockManager.assert_called_once_with(tmp_path)
            mock_manager.create.assert_called_once_with("T170", "developer", None, "HEAD")

    def test_remove_worktree(self, tmp_path: Path):
        with patch("orchid.worktree.WorktreeManager") as MockManager:
            mock_manager = MagicMock()
            mock_manager.remove.return_value = "removed"
            MockManager.return_value = mock_manager
            from orchid.worktree import remove_worktree
            result = remove_worktree(tmp_path, "T170")
            assert result == "removed"
            mock_manager.remove.assert_called_once_with("T170", None)

    def test_list_worktrees(self, tmp_path: Path):
        with patch("orchid.worktree.WorktreeManager") as MockManager:
            mock_manager = MagicMock()
            mock_manager.list_worktrees.return_value = [{"branch": "wt-T170-developer"}]
            MockManager.return_value = mock_manager
            from orchid.worktree import list_worktrees
            result = list_worktrees(tmp_path)
            assert result == [{"branch": "wt-T170-developer"}]
