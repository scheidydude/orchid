"""Git tools — safe, read-only git operations for the Orchid framework.

Provides:
- git_status: show working-tree status
- git_diff: show staged/unstaged changes
- git_log: show commit history
- git_show: show a single commit or tree object
- git_branches: list local/remote branches
- git_tags: list tags
- git_remote: show remote URLs
- git_stash_list: list stashed changes
- git_blame: show line-by-line attribution
- git_check_ignore: check if paths are git-ignored
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchid.errors import ToolError


def _git_cmd(args: list[str], cwd: Path | None = None, timeout: int = 30) -> str:
    """Run a git command and return combined stdout+stderr."""
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output.strip()
    except subprocess.TimeoutExpired:
        return f"[git command timed out after {timeout}s: {' '.join(cmd)}]"
    except FileNotFoundError:
        raise ToolError("git is not installed or not in PATH")


def git_status(
    path: str = ".",
    short: bool = False,
    untracked_files: str = "all",
) -> str:
    """Show the working-tree status.

    Args:
        path: Directory to run git status in (default '.').
        short: Use short format (one line per entry).
        untracked_files: one of 'all', 'normal', 'no'.
    """
    args = ["status", "--untracked-files=" + untracked_files]
    if short:
        args.insert(1, "--short")
    return _git_cmd(args, cwd=Path(path))


def git_diff(
    path: str = ".",
    staged: bool = False,
    cached: bool = False,
    include_untracked: bool = False,
) -> str:
    """Show changes between commits, working tree, etc.

    Args:
        path: Directory to run git diff in (default '.').
        staged: Show only staged changes (alias for --cached).
        cached: Same as staged.
        include_untracked: Include untracked file diffs.
    """
    args: list[str] = []
    if staged or cached:
        args.append("--cached")
    if include_untracked:
        args.append("--untracked-files")
    return _git_cmd(["diff"] + args, cwd=Path(path))


def git_log(
    path: str = ".",
    max_count: int = 20,
    oneline: bool = True,
    author: str | None = None,
    since: str | None = None,
    until: str | None = None,
    file_filter: str | None = None,
) -> str:
    """Show commit history.

    Args:
        path: Directory to run git log in (default '.').
        max_count: Maximum number of commits to show.
        oneline: Use --oneline format.
        author: Filter by author name.
        since: Show commits newer than a date (e.g. '2 weeks ago').
        until: Show commits older than a date.
        file_filter: Show only commits touching this file/path.
    """
    args: list[str] = ["log"]
    if oneline:
        args.append("--oneline")
    args.extend(["-n", str(max_count)])
    if author:
        args.extend(["--author", author])
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    if file_filter:
        args.extend(["--", file_filter])
    return _git_cmd(args, cwd=Path(path))


def git_show(
    path: str = ".",
    commit: str = "HEAD",
    stat: bool = False,
) -> str:
    """Show a single commit or tree object.

    Args:
        path: Directory to run git show in (default '.').
        commit: Commit SHA, tag, or ref (default 'HEAD').
        stat: Include a diffstat summary.
    """
    args: list[str] = ["show"]
    if stat:
        args.append("--stat")
    args.append(commit)
    return _git_cmd(args, cwd=Path(path))


def git_branches(
    path: str = ".",
    remote: bool = False,
    all_branches: bool = False,
) -> str:
    """List local and/or remote branches.

    Args:
        path: Directory to run git branch in (default '.').
        remote: Show remote branches (--remotes).
        all_branches: Show all branches including remote (--all).
    """
    args: list[str] = ["branch"]
    if remote:
        args.append("--remotes")
    elif all_branches:
        args.append("--all")
    return _git_cmd(args, cwd=Path(path))


def git_tags(
    path: str = ".",
    sort_by_date: bool = False,
) -> str:
    """List tags.

    Args:
        path: Directory to run git tag in (default '.').
        sort_by_date: Sort tags by creation date.
    """
    args: list[str] = ["tag"]
    if sort_by_date:
        args.append("--sort=-creatordate")
    return _git_cmd(args, cwd=Path(path))


def git_remote(
    path: str = ".",
    verbose: bool = False,
) -> str:
    """Show remote repository URLs.

    Args:
        path: Directory to run git remote in (default '.').
        verbose: Show fetch/push URLs (--verbose).
    """
    args: list[str] = ["remote"]
    if verbose:
        args.append("-v")
    return _git_cmd(args, cwd=Path(path))


def git_stash_list(
    path: str = ".",
    show_stat: bool = False,
) -> str:
    """List stashed changes.

    Args:
        path: Directory to run git stash in (default '.').
        show_stat: Include a diffstat for each stash entry.
    """
    args: list[str] = ["stash", "list"]
    if show_stat:
        args.append("--stat")
    return _git_cmd(args, cwd=Path(path))


def git_blame(
    path: str = ".",
    file_path: str | None = None,
    line_range: tuple[int, int] | None = None,
    ignore_whitespace: bool = False,
) -> str:
    """Show line-by-line attribution for a file.

    Args:
        path: Directory to run git blame in (default '.').
        file_path: The file to blame (required).
        line_range: (start, end) line numbers to show.
        ignore_whitespace: Ignore whitespace changes.
    """
    if not file_path:
        raise ToolError("git_blame requires file_path argument")
    args: list[str] = ["blame"]
    if ignore_whitespace:
        args.append("-w")
    if line_range:
        start, end = line_range
        args.extend(["-L", f"{start},{end}"])
    args.append(file_path)
    return _git_cmd(args, cwd=Path(path))


def git_check_ignore(
    path: str = ".",
    *file_paths: str,
) -> str:
    """Check if paths are ignored by .gitignore.

    Args:
        path: Directory to run git check-ignore in (default '.').
        file_paths: One or more paths to check.
    """
    if not file_paths:
        raise ToolError("git_check_ignore requires at least one file path")
    args: list[str] = ["check-ignore", "-v"] + list(file_paths)
    return _git_cmd(args, cwd=Path(path))


def git_ls_files(
    path: str = ".",
    cached: bool = False,
    stage: int | None = None,
) -> str:
    """List files in the index and working tree.

    Args:
        path: Directory to run git ls-files in (default '.').
        cached: Show only files in the index.
        stage: Show files at a specific stage (0=normal, 1=ours, 2=theirs, 3=merged).
    """
    args: list[str] = ["ls-files"]
    if cached:
        args.append("--cached")
    if stage is not None:
        args.extend(["--stage"])
    return _git_cmd(args, cwd=Path(path))


def git_diff_summary(
    path: str = ".",
    commit_a: str = "HEAD",
    commit_b: str | None = None,
) -> str:
    """Show a summary of file changes between two commits.

    Args:
        path: Directory to run git diff in (default '.').
        commit_a: First commit/ref.
        commit_b: Second commit/ref (default None, which compares against working tree).
    """
    args: list[str] = ["diff", "--stat", "--numstat"]
    if commit_b:
        args.append(f"{commit_a}..{commit_b}")
    else:
        args.append(commit_a)
    return _git_cmd(args, cwd=Path(path))
