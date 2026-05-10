"""Worktree manager — create isolated git worktrees for parallel task execution.

Provides:
- WorktreeManager: create, list, switch, and remove worktrees
- Automatic worktree creation for delegated sub-tasks
- Safe cleanup when worktrees are no longer needed

Usage:
    manager = WorktreeManager(project_dir)
    wt_path = manager.create("feature-branch")
    # ... agent works in wt_path ...
    manager.remove("feature-branch")
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchid import config as cfg
from orchid.errors import ToolError

logger = logging.getLogger(__name__)


class WorktreeError(Exception):
    pass


@dataclass
class WorktreeInfo:
    """Metadata about a managed worktree."""
    branch: str
    path: Path
    created_at: str = ""
    task_id: str = ""
    status: str = "active"  # active | detached | removed

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "path": str(self.path),
            "created_at": self.created_at,
            "task_id": self.task_id,
            "status": self.status,
        }


class WorktreeManager:
    """
    Manages git worktrees for isolated task execution.

    Each worktree is a separate working directory linked to a git branch.
    This allows multiple agents to work on different branches in parallel
    without interfering with each other.

    Worktrees are stored under <project_dir>/.orchid/worktrees/ to keep
    them organized and easy to clean up.

    Naming convention: wt-{task_id}-{agent_type}
    e.g. wt-T170-developer, wt-T171-developer
    """

    # Maximum depth of git worktrees (git limitation)
    MAX_DEPTH = 10

    def __init__(self, project_dir: Path | str, max_worktrees: int | None = None):
        self.project_dir = Path(project_dir).resolve()
        self.worktrees_dir = self.project_dir / ".orchid" / "worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self._worktrees: dict[str, WorktreeInfo] = {}
        self.max_worktrees = max_worktrees or cfg.get("worktree.max_worktrees", 10)
        self._auto_cleanup = cfg.get("worktree.auto_cleanup", True)
        self._load_existing()

    # ── Core operations ────────────────────────────────────────────────────────

    def create(
        self,
        task_id: str,
        agent_type: str = "developer",
        branch_name: str | None = None,
        base_ref: str = "HEAD",
    ) -> Path:
        """
        Create a new git worktree for the given task.

        Args:
            task_id: Task identifier (e.g. "T170").
            agent_type: Agent type for naming (e.g. "developer").
            branch_name: Optional custom branch name. Auto-generated if None.
            base_ref: The git ref to branch from (default "HEAD").

        Returns:
            Path to the new worktree directory.

        Raises:
            ToolError: If worktree creation fails or limits are exceeded.
        """
        # Sanitize task_id to prevent path escape via ../ or embedded slashes
        task_id = task_id.replace("/", "_").replace("..", "_")

        if len(self._worktrees) >= self.max_worktrees:
            if self._auto_cleanup:
                self._cleanup_oldest()
            else:
                raise ToolError(
                    f"Worktree limit reached ({self.max_worktrees}). "
                    "Set worktree.auto_cleanup=true to auto-remove oldest worktrees."
                )

        if branch_name is None:
            branch_name = f"wt-{task_id}-{agent_type}"

        # Check if branch already exists as a worktree branch
        if self._worktree_branch_exists(branch_name):
            logger.info("Worktree branch %s already exists — reusing", branch_name)
            existing = self._worktrees.get(branch_name)
            if existing and existing.status == "active":
                return existing.path

        wt_path = self.worktrees_dir / f"wt-{task_id}-{agent_type}"
        wt_path.mkdir(parents=True, exist_ok=True)

        cmd = [
            "git", "worktree", "add",
            "-b", branch_name,
            str(wt_path),
            base_ref,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self.project_dir,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                stdout = result.stdout.strip()
                msg = stderr or stdout or f"exit code {result.returncode}"
                raise ToolError(f"git worktree add failed: {msg}")
        except subprocess.TimeoutExpired:
            raise ToolError("git worktree add timed out after 30s")
        except FileNotFoundError:
            raise ToolError("git is not installed or not in PATH")

        info = WorktreeInfo(
            branch=branch_name,
            path=wt_path,
            created_at=datetime.now(UTC).isoformat(),
            task_id=task_id,
            status="active",
        )
        self._worktrees[branch_name] = info
        logger.info(
            "Created worktree: branch=%s path=%s task=%s",
            branch_name, wt_path, task_id,
        )
        return wt_path

    def remove(self, task_id: str, branch_name: str | None = None) -> str:
        """
        Remove a worktree by task_id (or branch_name).

        Args:
            task_id: The task identifier whose worktree to remove.
            branch_name: Optional branch name override.

        Returns:
            Status message.
        """
        task_id = task_id.replace("/", "_").replace("..", "_")
        info = self._find_by_task_id(task_id)
        if not info:
            if branch_name:
                info = self._worktrees.get(branch_name)
            if not info:
                return f"[worktree not found: task={task_id} branch={branch_name}]"

        branch = info.branch
        wt_path = info.path

        cmd = ["git", "worktree", "remove", "--force", str(wt_path)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self.project_dir,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                raise ToolError(f"git worktree remove failed: {stderr}")
        except subprocess.TimeoutExpired:
            raise ToolError("git worktree remove timed out after 30s")
        except FileNotFoundError:
            raise ToolError("git is not installed or not in PATH")

        # Clean up empty directory
        try:
            if wt_path.exists():
                wt_path.rmdir()
        except OSError:
            pass  # directory may have files left over

        info.status = "removed"
        logger.info("Removed worktree: branch=%s task=%s", branch, task_id)
        return f"Worktree removed: {branch} at {wt_path}"

    def list_worktrees(self) -> list[dict[str, Any]]:
        """
        List all managed worktrees.

        Returns:
            List of worktree info dicts.
        """
        return [wt.to_dict() for wt in self._worktrees.values() if wt.status == "active"]

    def get_worktree_path(self, task_id: str) -> Path | None:
        """
        Get the worktree path for a task_id.

        Args:
            task_id: The task identifier.

        Returns:
            Path to the worktree, or None if not found.
        """
        info = self._find_by_task_id(task_id)
        return info.path if info else None

    def get_worktree_branch(self, task_id: str) -> str | None:
        """
        Get the branch name for a task_id.

        Args:
            task_id: The task identifier.

        Returns:
            Branch name, or None if not found.
        """
        info = self._find_by_task_id(task_id)
        return info.branch if info else None

    # ── Bulk operations ────────────────────────────────────────────────────────

    def remove_all(self) -> str:
        """
        Remove all active worktrees.

        Returns:
            Status message.
        """
        removed = []
        for branch, info in list(self._worktrees.items()):
            if info.status == "active":
                try:
                    cmd = ["git", "worktree", "remove", "--force", str(info.path)]
                    subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=self.project_dir,
                    )
                    info.status = "removed"
                    removed.append(branch)
                except Exception as e:
                    logger.warning("Failed to remove worktree %s: %s", branch, e)

        # Clean up empty directories
        try:
            for entry in self.worktrees_dir.iterdir():
                if entry.is_dir():
                    try:
                        entry.rmdir()
                    except OSError:
                        pass  # not empty
        except OSError:
            pass

        if removed:
            return f"Removed {len(removed)} worktree(s): {', '.join(removed)}"
        return "No active worktrees to remove"

    def prune(self) -> str:
        """
        Prune worktrees that no longer exist in git (orphaned entries).

        Returns:
            Status message.
        """
        pruned = []
        active_branches = self._list_git_worktree_branches()

        for branch, info in list(self._worktrees.items()):
            if info.status == "active" and branch not in active_branches:
                info.status = "removed"
                pruned.append(branch)

        if pruned:
            return f"Pruned {len(pruned)} orphaned worktree(s): {', '.join(pruned)}"
        return "No orphaned worktrees found"

    # ── Git operations on worktrees ────────────────────────────────────────────

    def commit_worktree(
        self,
        task_id: str,
        message: str = "worktree: task completion",
        branch_name: str | None = None,
    ) -> str:
        """
        Stage all changes and commit in a worktree.

        Args:
            task_id: The task identifier.
            message: Commit message.
            branch_name: Optional branch name override.

        Returns:
            Commit output.
        """
        info = self._find_by_task_id(task_id)
        if not info:
            return f"[worktree not found: task={task_id}]"

        # Stage all changes
        stage_cmd = ["git", "add", "."]
        subprocess.run(
            stage_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=info.path,
        )

        # Commit
        commit_cmd = ["git", "commit", "-m", message]
        result = subprocess.run(
            commit_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=info.path,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            # "nothing to commit" is not an error
            if "nothing to commit" in stderr or "nothing added" in stderr:
                return f"[no changes to commit in worktree {info.branch}]"
            raise ToolError(f"git commit failed: {stderr}")

        return f"[committed in {info.branch}]: {message}"

    def diff_worktree(self, task_id: str, branch_name: str | None = None) -> str:
        """
        Show diff of changes in a worktree.

        Args:
            task_id: The task identifier.
            branch_name: Optional branch name override.

        Returns:
            Diff output.
        """
        info = self._find_by_task_id(task_id)
        if not info:
            return f"[worktree not found: task={task_id}]"

        cmd = ["git", "diff", "--stat"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=info.path,
        )
        return result.stdout.strip() or "[no changes]"

    def status_worktree(self, task_id: str, branch_name: str | None = None) -> str:
        """
        Show working-tree status in a worktree.

        Args:
            task_id: The task identifier.
            branch_name: Optional branch name override.

        Returns:
            Status output.
        """
        info = self._find_by_task_id(task_id)
        if not info:
            return f"[worktree not found: task={task_id}]"

        cmd = ["git", "status", "--short"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=info.path,
        )
        return result.stdout.strip() or "[working tree clean]"

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _find_by_task_id(self, task_id: str) -> WorktreeInfo | None:
        """Find a worktree info by task_id."""
        for info in self._worktrees.values():
            if info.task_id == task_id and info.status == "active":
                return info
        return None

    def _worktree_branch_exists(self, branch_name: str) -> bool:
        """Check if a branch exists specifically as a git worktree branch.

        Unlike _branch_exists which checks ALL refs, this only checks branches
        that are active worktrees. This avoids false positives from regular
        branches with the same name.
        """
        branches = self._list_git_worktree_branches()
        return branch_name in branches

    def _list_git_worktree_branches(self) -> set[str]:
        """List all branches that are git worktrees."""
        cmd = ["git", "worktree", "list", "--porcelain"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=self.project_dir,
        )
        branches: set[str] = set()
        current_branch: str | None = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current_branch = None
            elif line.startswith("branch refs/heads/"):
                current_branch = line[len("branch refs/heads/"):]
                if current_branch:
                    branches.add(current_branch)
        return branches

    def _load_existing(self) -> None:
        """Scan for existing worktrees and register them.

        Uses git worktree list --porcelain to get actual paths and branch names,
        then matches wt-* branches to our naming convention.
        """
        # Parse porcelain output to get (branch, path) pairs
        wt_pairs: list[tuple[str, str]] = []
        current_branch: str | None = None
        current_path: str | None = None
        for line in self._list_git_worktree_branches_raw():
            if line.startswith("worktree "):
                if current_branch and current_path:
                    wt_pairs.append((current_branch, current_path))
                current_branch = None
                current_path = None
            elif line.startswith("branch refs/heads/"):
                current_branch = line[len("branch refs/heads/"):]
            elif line.startswith("HEAD "):
                current_path = line[len("HEAD "):]

        # Don't forget the last entry
        if current_branch and current_path:
            wt_pairs.append((current_branch, current_path))

        for branch, path in wt_pairs:
            if branch.startswith("wt-"):
                # Parse task_id and agent_type from branch name
                match = re.match(r"wt-([^-]+)-(.+)", branch)
                if match:
                    task_id = match.group(1)
                    wt_path = Path(path).resolve()
                    info = WorktreeInfo(
                        branch=branch,
                        path=wt_path,
                        created_at="",
                        task_id=task_id,
                        status="active",
                    )
                    self._worktrees[branch] = info
                    logger.info("Loaded existing worktree: branch=%s path=%s task=%s", branch, wt_path, task_id)

    def _list_git_worktree_branches_raw(self) -> str:
        """Get raw porcelain output from git worktree list."""
        cmd = ["git", "worktree", "list", "--porcelain"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=self.project_dir,
        )
        return result.stdout

    def _cleanup_oldest(self) -> None:
        """Remove the oldest active worktree to make room.

        Oldest is determined by created_at timestamp. Worktrees loaded from
        existing git (with empty created_at) are excluded from cleanup since
        we can't determine their age.
        """
        if not self._worktrees:
            return

        oldest_branch = None
        oldest_time: str | None = None
        for branch, info in self._worktrees.items():
            if info.status != "active":
                continue
            # Skip worktrees with no timestamp (loaded from existing git)
            if not info.created_at:
                continue
            if oldest_time is None or info.created_at < oldest_time:
                oldest_branch = branch
                oldest_time = info.created_at

        if oldest_branch:
            logger.info("Auto-removing oldest worktree: %s", oldest_branch)
            self.remove_by_branch(oldest_branch)

    def remove_by_branch(self, branch_name: str) -> str:
        """Remove a worktree by branch name."""
        info = self._worktrees.get(branch_name)
        if not info or info.status != "active":
            return f"[worktree not found or inactive: {branch_name}]"

        task_id = info.task_id
        return self.remove(task_id, branch_name)


# ── Convenience functions ─────────────────────────────────────────────────────

def create_worktree(
    project_dir: str | Path,
    task_id: str,
    agent_type: str = "developer",
    branch_name: str | None = None,
    base_ref: str = "HEAD",
) -> Path:
    """
    Create a worktree for a task. Convenience wrapper around WorktreeManager.

    Args:
        project_dir: Project directory path.
        task_id: Task identifier.
        agent_type: Agent type for naming.
        branch_name: Optional custom branch name.
        base_ref: Git ref to branch from.

    Returns:
        Path to the worktree directory.
    """
    manager = WorktreeManager(project_dir)
    return manager.create(task_id, agent_type, branch_name, base_ref)


def remove_worktree(
    project_dir: str | Path,
    task_id: str,
    branch_name: str | None = None,
) -> str:
    """
    Remove a worktree for a task. Convenience wrapper around WorktreeManager.

    Args:
        project_dir: Project directory path.
        task_id: Task identifier.
        branch_name: Optional branch name override.

    Returns:
        Status message.
    """
    manager = WorktreeManager(project_dir)
    return manager.remove(task_id, branch_name)


def list_worktrees(project_dir: str | Path) -> list[dict[str, Any]]:
    """
    List all managed worktrees. Convenience wrapper around WorktreeManager.

    Args:
        project_dir: Project directory path.

    Returns:
        List of worktree info dicts.
    """
    manager = WorktreeManager(project_dir)
    return manager.list_worktrees()