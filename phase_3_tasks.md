# Phase 3 — Subagent Worktree Isolation

**Deploy after phase: Yes** — feature is opt-in via config (`delegation.worktree_isolation: false` by default). No behavior changes unless enabled.
**Pre-deploy check:** `pytest tests/test_worktree.py` passes.

---

- [ ] **T170** Create `orchid/worktree.py` `type:code_generate` `p1` `model:local`

Create new file `orchid/worktree.py`. Define exactly one class and one exception.

**`WorktreeError(Exception)`**: body is `pass`.

**`WorktreeManager`**:
Constructor: `__init__(self, project_dir: Path | str)`. Sets:
- `self._project_dir = Path(project_dir).resolve()`
- `self._worktrees_dir = self._project_dir / ".orchid" / "worktrees"`
- `self._failed_dir = self._project_dir / ".orchid" / "worktrees" / "_failed"`

**`create(self, task_id: str, base_branch: str = "") -> Path`**:
- Creates `self._worktrees_dir / task_id` directory path (does not mkdir — git worktree add does that).
- Runs `git worktree add <path> <base_branch>` if base_branch is non-empty, else `git worktree add <path>` (uses current HEAD).
- Uses `_run_git(["worktree", "add", str(wt_path)] + ([base_branch] if base_branch else []))`.
- On non-zero exit: raise `WorktreeError(f"git worktree add failed: {stderr}")`.
- Returns `wt_path: Path`.

**`merge(self, task_id: str, strategy_message: str = "") -> str`**:
- Gets `wt_path = self._worktrees_dir / task_id`.
- Gets current branch name in worktree: `_run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt_path)`.
- Switches back to main project dir, merges worktree branch: `_run_git(["merge", "--no-ff", wt_branch, "-m", strategy_message or f"Merge worktree {task_id}"], cwd=self._project_dir)`.
- On success: calls `self.cleanup(task_id)`.
- Returns merge output string.
- On non-zero exit: raise `WorktreeError(f"merge failed: {stderr}")`.

**`cleanup(self, task_id: str) -> None`**:
- Runs `git worktree remove --force <path>`.
- Uses `_run_git(["worktree", "remove", "--force", str(self._worktrees_dir / task_id)])`.
- Errors are logged as warnings, not raised.

**`keep_for_inspection(self, task_id: str) -> str`**:
- Moves worktree dir to `_failed_dir / task_id` (create `_failed_dir` if needed).
- Runs `git worktree list --porcelain` and logs the path.
- Runs `_run_git(["worktree", "move", str(self._worktrees_dir / task_id), str(self._failed_dir / task_id)])`.
- On error (git worktree move may not exist in older git): fall back to `shutil.move(...)`.
- Returns `str(self._failed_dir / task_id)`.

**`_run_git(self, args: list[str], cwd: Path | None = None) -> tuple[int, str, str]`**:
- Runs `subprocess.run(["git"] + args, capture_output=True, text=True, cwd=str(cwd or self._project_dir))`.
- Returns `(returncode, stdout.strip(), stderr.strip())`.

Imports: `from __future__ import annotations`, `import logging`, `import shutil`, `import subprocess`, `from pathlib import Path`.
`logger = logging.getLogger(__name__)`.

---

- [ ] **T171** Add worktree config to `orchid/orchid.defaults.yaml` `type:code_generate` `p1` `model:local`

Read `orchid/orchid.defaults.yaml`. Append after the `git_tools:` section added in T166:

```yaml

# Worktree isolation for delegated sub-tasks.
# When enabled, each delegate() call creates an isolated git worktree.
# On success: auto-merge back to original branch and delete worktree.
# On failure: move worktree to .orchid/worktrees/_failed/<task_id> for inspection.
delegation:
  worktree_isolation: false          # set true to enable
  worktree_base_dir: ".orchid/worktrees"
  worktree_base_branch: ""           # empty = use current HEAD
  worktree_merge_message: "feat: merge delegated worktree {task_id}"
```

---

- [ ] **T172** Wire WorktreeManager into `AgentDelegator.delegate()` `type:code_generate` `p1` `needs:T170,T171` `model:local`

Read `orchid/agents/delegator.py` first.

Make exactly one change to `AgentDelegator.delegate()`. After the line `result = agent.run(task)` and before `result_summary = result[:500]`, add:

```python
        # Worktree cleanup on success (if isolation was active)
        if _wt_manager is not None and _wt_task_id is not None:
            try:
                _merge_msg = cfg.get(
                    "delegation.worktree_merge_message",
                    "feat: merge delegated worktree {task_id}",
                ).format(task_id=_wt_task_id)
                _wt_manager.merge(_wt_task_id, strategy_message=_merge_msg)
            except Exception as _wte:
                logger.warning("[delegator] worktree merge failed: %s", _wte)
```

Also, at the beginning of `delegate()`, after the `if depth >= max_depth:` block, add worktree setup:

```python
        # Worktree isolation setup
        _wt_manager = None
        _wt_task_id = None
        if cfg.get("delegation.worktree_isolation", False) and self.session:
            from orchid.worktree import WorktreeManager, WorktreeError
            import uuid as _uuid
            _wt_task_id = f"del-{_uuid.uuid4().hex[:8]}"
            _wt_manager = WorktreeManager(self.session.project_dir)
            try:
                _base = cfg.get("delegation.worktree_base_branch", "")
                _wt_path = _wt_manager.create(_wt_task_id, base_branch=_base)
                # Run agent inside worktree directory
                kwargs_override = {"project_dir": _wt_path}
            except WorktreeError as _wte:
                logger.warning(
                    "[delegator] worktree create failed, running in project dir: %s", _wte
                )
                _wt_manager = None
                _wt_task_id = None
```

Also wrap `agent.run(task)` in a try/except so that on exception, worktree is kept for inspection:

```python
        try:
            result = agent.run(task)
        except Exception as _agent_exc:
            if _wt_manager is not None and _wt_task_id is not None:
                _kept = _wt_manager.keep_for_inspection(_wt_task_id)
                logger.error("[delegator] agent failed; worktree kept at %s", _kept)
            raise
```

Replace the plain `result = agent.run(task)` line with this try/except block.

When `_wt_path` is set (worktree created successfully), pass `project_dir=_wt_path` to the agent constructor. Find where `agent = agent_cls(...)` is called and add `project_dir=_wt_path` if `_wt_manager is not None`, else keep existing `project_dir=project_dir`.

---

- [ ] **T173** Create `tests/test_worktree.py` `type:code_generate` `p1` `needs:T170` `model:local`

Create file `tests/test_worktree.py`. Write exactly 4 test functions using `unittest.mock.patch` to mock `subprocess.run`. Do not run actual git commands.

```python
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from orchid.worktree import WorktreeError, WorktreeManager
```

**`test_create_runs_git_worktree_add(tmp_path)`**: mock `subprocess.run` to return `MagicMock(returncode=0, stdout="", stderr="")`. Call `WorktreeManager(tmp_path).create("T001")`. Assert `subprocess.run` was called with args list containing `"worktree"` and `"add"`.

**`test_create_raises_on_nonzero(tmp_path)`**: mock returns `MagicMock(returncode=128, stdout="", stderr="fatal: not a git repo")`. Call `WorktreeManager(tmp_path).create("T001")`. Assert `WorktreeError` is raised.

**`test_cleanup_calls_worktree_remove(tmp_path)`**: mock returns success. Call `WorktreeManager(tmp_path).cleanup("T001")`. Assert subprocess.run was called with args containing `"worktree"`, `"remove"`, `"--force"`.

**`test_cleanup_does_not_raise_on_error(tmp_path)`**: mock returns `MagicMock(returncode=1, stdout="", stderr="")`. Call `WorktreeManager(tmp_path).cleanup("T001")`. Assert no exception is raised.

---

- [ ] **T174** Review worktree implementation `type:code_review` `p1` `needs:T173`

Review files: `orchid/worktree.py`, `orchid/agents/delegator.py` (worktree wiring only).

Check for exactly these issues:
1. **Worktree path escape** — could `task_id` contain `..` or `/` to escape `_worktrees_dir`? Report FAIL if `_worktrees_dir / task_id` is not validated.
2. **merge() cleanup on failure** — if merge fails (WorktreeError raised), is the worktree still cleaned up or left dangling? Report the behavior.
3. **agent project_dir assignment** — when worktree is created, does the agent receive `project_dir=_wt_path` correctly? Or does it still receive the original project_dir?
4. **keep_for_inspection fallback** — if `git worktree move` fails and shutil.move is used, does the worktree remain registered in git's worktree list (potentially causing issues)? Report if this is a risk.

Report each as PASS or FAIL with file and line number.

---

- [ ] **T175** Fix issues found in T174 `type:code_generate` `p1` `needs:T174` `model:local`

Read T174 review results. For each FAIL, apply minimal fix. For issue 1 (path escape): sanitize `task_id` by replacing any `/` or `..` with `_` at the start of `create()` and `cleanup()`: `task_id = task_id.replace("/", "_").replace("..", "_")`. For issue 4 (keep_for_inspection git registration): after `shutil.move`, run `git worktree prune` to clean up stale registrations. Apply only fixes for flagged FAILs. If no FAILs, write `Final Answer: No fixes needed.`
