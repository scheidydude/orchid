# Phase 2 — Native Git Integration

**Deploy after phase: Yes** — additive tools only, no existing behavior changes.
**Pre-deploy check:** `pytest tests/test_git_tools.py` passes. Run `orchid --check-providers` confirms no regressions.

---

- [ ] **T163** Create `orchid/tools/git.py` `type:code_generate` `p1` `model:local`

Create new file `orchid/tools/git.py`. Implement exactly these 8 functions using `subprocess.run` with `capture_output=True, text=True`. All functions accept `repo_path: str = "."` as last parameter and run git commands with `cwd=repo_path`. On non-zero exit code, include `[exit N]` and stderr in return string. On `FileNotFoundError` (git not installed), return `"[git not found]"`. No exceptions should propagate out of any function.

```python
from __future__ import annotations
import subprocess
from orchid.errors import ToolError  # do NOT raise ToolError — only use for type reference if needed

def _git(args: list[str], repo_path: str = ".") -> str:
    """Run git with args, return stdout+stderr. Never raises."""
    try:
        r = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, cwd=repo_path,
        )
        out = r.stdout.strip()
        if r.returncode != 0:
            err = r.stderr.strip()
            return f"{out}\n[exit {r.returncode}]: {err}".strip()
        return out or "[ok]"
    except FileNotFoundError:
        return "[git not found]"
    except Exception as e:
        return f"[git error: {e}]"
```

**8 public functions** (all call `_git()`):

```python
def git_status(repo_path: str = ".") -> str:
    """Return working tree status (git status --short)."""
    return _git(["status", "--short"], repo_path)

def git_diff(repo_path: str = ".", staged: bool = False) -> str:
    """Return diff of working tree or staged changes."""
    args = ["diff"] if not staged else ["diff", "--cached"]
    return _git(args, repo_path)

def git_add(paths: str, repo_path: str = ".") -> str:
    """Stage one or more paths (space-separated). paths='.' stages all."""
    return _git(["add"] + paths.split(), repo_path)

def git_commit(message: str, repo_path: str = ".") -> str:
    """Create a commit with message. Returns commit sha on success."""
    return _git(["commit", "-m", message], repo_path)

def git_branch(name: str = "", repo_path: str = ".") -> str:
    """List branches if name is empty, create branch if name is given."""
    if name:
        return _git(["checkout", "-b", name], repo_path)
    return _git(["branch", "--list"], repo_path)

def git_checkout(branch: str, repo_path: str = ".") -> str:
    """Switch to an existing branch."""
    return _git(["checkout", branch], repo_path)

def git_push(remote: str = "origin", branch: str = "", repo_path: str = ".") -> str:
    """Push to remote. If branch is empty, pushes current branch."""
    args = ["push", remote]
    if branch:
        args.append(branch)
    return _git(args, repo_path)

def git_log(n: int = 10, repo_path: str = ".") -> str:
    """Return last n commit log entries (oneline format)."""
    return _git(["log", f"--max-count={n}", "--oneline"], repo_path)
```

No other functions, no classes, no imports besides `subprocess` and `__future__`.

---

- [ ] **T164** Register git tools in `_make_project_tools` in `orchid/agents/base.py` `type:code_generate` `p1` `needs:T163` `model:local`

Read `orchid/agents/base.py` first.

Make exactly two changes:

**Change 1** — add import near the other tool imports at the top of the file:
```python
from orchid.tools.git import (
    git_add, git_branch, git_checkout, git_commit,
    git_diff, git_log, git_push, git_status,
)
```

**Change 2** — inside `_make_project_tools(project_dir: Path)`, before the `return {` statement, add git tool wrappers that anchor `repo_path` to `project_dir`:
```python
_repo = str(project_dir)

def _git_status() -> str:
    return git_status(repo_path=_repo)

def _git_diff(staged: bool = False) -> str:
    return git_diff(repo_path=_repo, staged=staged)

def _git_add(paths: str = ".") -> str:
    return git_add(paths=paths, repo_path=_repo)

def _git_commit(message: str) -> str:
    return git_commit(message=message, repo_path=_repo)

def _git_branch(name: str = "") -> str:
    return git_branch(name=name, repo_path=_repo)

def _git_checkout(branch: str) -> str:
    return git_checkout(branch=branch, repo_path=_repo)

def _git_push(remote: str = "origin", branch: str = "") -> str:
    return git_push(remote=remote, branch=branch, repo_path=_repo)

def _git_log(n: int = 10) -> str:
    return git_log(n=n, repo_path=_repo)
```

Then add these to the returned dict:
```python
"git_status": _git_status,
"git_diff": _git_diff,
"git_add": _git_add,
"git_commit": _git_commit,
"git_branch": _git_branch,
"git_checkout": _git_checkout,
"git_push": _git_push,
"git_log": _git_log,
```

Do not modify anything else. These tools are unrestricted at the base level; `DeveloperAgent` gets all, others get them filtered by Phase 1 `allowed_tools`.

**Verification (required before Final Answer):** After writing, run:
```
bash("grep -n 'git_status\|git_commit\|from orchid.tools.git' orchid/agents/base.py")
```
If grep returns fewer than 3 matching lines, the write failed. Re-read `orchid/agents/base.py`, find the exact location, and retry. Only give Final Answer after grep confirms all three symbols are present.

---

- [ ] **T165** Add git tools to DeveloperAgent `allowed_tools` and system prompt `type:code_generate` `p1` `needs:T164,T156` `model:local`

Read `orchid/agents/developer.py` and `orchid/agents/reviewer.py`.

**Change 1** — DeveloperAgent has no `allowed_tools` class variable (all tools allowed). Add a git-aware section to its `system_prompt()`. After the existing `## Workflow` section in the returned string, add:

```
## Git Integration
You have first-class git tools available: git_status, git_diff, git_add, git_commit, git_branch, git_checkout, git_push, git_log.
Use these instead of bash git commands when possible. Example:
  Action: git_status
  Action: git_commit
  Action Input: {"message": "feat: add new parser"}
Only push (git_push) if the task explicitly requires it.
```

**Change 2** — ReviewerAgent `allowed_tools` was set in T156 to `frozenset({"read_file", "list_dir", "bash", "check_imports", "get_task_files"})`. Add git read-only tools to it:
```python
allowed_tools: frozenset[str] | None = frozenset({
    "read_file", "list_dir", "bash", "check_imports", "get_task_files",
    "git_status", "git_diff", "git_log",
})
```
Do NOT add `git_add`, `git_commit`, `git_branch`, `git_checkout`, `git_push` to reviewer.

Also update TesterAgent in `orchid/agents/tester.py` the same way — add `"git_status", "git_diff", "git_log"` to its `allowed_tools` frozenset (read-only git is fine for test agents).

---

- [ ] **T166** Add `git_tools_enabled` config to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `model:local`

Read `orchid/orchid.defaults.yaml`. Append after the `hooks.circuit_breaker` section added in T157 (at end of file):

```yaml

# Git tools — first-class git operations available to agents.
# Set enabled: false to remove git_* tools from all agent tool registries.
git_tools:
  enabled: true
```

**Verification (required before Final Answer):** Run:
```
bash("grep -n 'git_tools' orchid/orchid.defaults.yaml")
```
If grep returns no output, the append failed. Re-read the end of the file and retry. Only give Final Answer after grep confirms `git_tools:` is present.

---

- [ ] **T166b** Wrap git tool registration in config guard in `orchid/agents/base.py` `type:code_generate` `p1` `needs:T164,T166` `model:local`

Read `orchid/agents/base.py`. Find `_make_project_tools(project_dir: Path)`. Find the block of git wrapper functions and dict entries added by T164 (starts with `_repo = str(project_dir)`, ends with `"git_log": _git_log,` in the return dict).

Make exactly one change: wrap the git wrapper definitions AND their dict entries in a config guard. The return dict currently has all tools listed. Split it so git tools are only added conditionally:

Before the `return {` statement, add:
```python
if cfg.get("git_tools.enabled", True):
    _repo = str(project_dir)
    # [all 8 git wrapper function definitions go here]
    _git_tools = {
        "git_status": _git_status,
        "git_diff": _git_diff,
        "git_add": _git_add,
        "git_commit": _git_commit,
        "git_branch": _git_branch,
        "git_checkout": _git_checkout,
        "git_push": _git_push,
        "git_log": _git_log,
    }
else:
    _git_tools = {}
```

Then in the `return {` dict, replace the individual `"git_status": _git_status, ...` entries with `**_git_tools,`.

The non-git tools remain in the return dict unconditionally. `cfg` is already imported at the top of `base.py`.

**Verification (required before Final Answer):** Run:
```
bash("grep -n 'git_tools.enabled\|_git_tools' orchid/agents/base.py")
```
If grep returns fewer than 2 lines, the write failed. Re-read `_make_project_tools` and retry. Only give Final Answer after grep confirms both symbols are present.

---

- [ ] **T167** Create `tests/test_git_tools.py` `type:code_generate` `p1` `needs:T163` `model:local`

Create file `tests/test_git_tools.py`. Write exactly 5 test functions using `unittest.mock.patch` to mock `subprocess.run`. Do not run actual git commands.

```python
from unittest.mock import MagicMock, patch
from orchid.tools.git import git_status, git_diff, git_add, git_commit, git_log
```

**`test_git_status_returns_stdout`**: mock `subprocess.run` returns `MagicMock(stdout="M  file.py\n", stderr="", returncode=0)`. Call `git_status()`. Assert result is `"M  file.py"`.

**`test_git_status_nonzero_includes_exit_code`**: mock returns `MagicMock(stdout="", stderr="not a repo", returncode=128)`. Call `git_status()`. Assert `"128"` in result and `"not a repo"` in result.

**`test_git_diff_staged_flag`**: capture args passed to `subprocess.run`. Assert when `staged=True`, args list contains `"--cached"`. Assert when `staged=False`, `"--cached"` not in args.

**`test_git_commit_passes_message`**: mock returns success. Call `git_commit("my message")`. Capture subprocess.run call args. Assert `"-m"` and `"my message"` both appear in the args list.

**`test_git_not_found_returns_message`**: mock `subprocess.run` to raise `FileNotFoundError`. Call `git_status()`. Assert result is `"[git not found]"`.

---

- [ ] **T168** Review git integration `type:code_review` `p1` `needs:T167,T166b`

Review files: `orchid/tools/git.py`, `orchid/agents/base.py` (git tool registration block only), `orchid/agents/developer.py` (git prompt section only), `orchid/agents/reviewer.py` and `orchid/agents/tester.py` (allowed_tools only).

Check for exactly these issues:
1. **Shell injection** — do any git functions construct commands by string concatenation with user-controlled inputs (message, paths, branch)? All args must be passed as list elements to `subprocess.run`, never via `shell=True`. Report PASS if `shell=True` is absent in git.py.
2. **Repo path escape** — could `repo_path` be set to a path outside the project? The wrappers in `_make_project_tools` hardcode `_repo = str(project_dir)` so callers cannot override. Report PASS if wrappers don't expose `repo_path` to the agent.
3. **git_push in reviewer** — is `git_push` absent from ReviewerAgent and TesterAgent `allowed_tools`? Report PASS or FAIL with line number.
4. **git_tools.enabled guard** — does `_make_project_tools` correctly skip all git tools when `cfg.get("git_tools.enabled", True)` is False? Report PASS or FAIL.

Report each as PASS or FAIL with file and line number.

---

- [ ] **T169** Fix issues found in T168 `type:code_generate` `p1` `needs:T168` `model:local`

Read T168 review results from `.orchid/task_results.json`. For each FAIL, apply the minimal fix to the flagged file and line. Do not refactor anything not flagged. If no FAILs, write `Final Answer: No fixes needed.` immediately.
