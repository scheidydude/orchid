# Tier 4 — Scale Tasks
# Remote worker protocol · Agent capability manifest · Distributed cost ledger · Task migration
# Starts at T266 (after Tier 3 T265). Copy this file content into tasks.md and run.
# Claude Code validates after this tier completes. This tier is the most architectural — review carefully.

## DONE

## TODO

- [ ] **T266** Create `orchid/remote/__init__.py` with content `# Remote worker protocol` and `orchid/remote/types.py`. `type:code_generate` `p1` `model:local`
  - `orchid/remote/__init__.py` content: exactly `# Remote worker protocol`
  - `orchid/remote/types.py` imports: `from dataclasses import dataclass, field`. `from typing import Any`
  - `@dataclass class WorkerNode:` — `node_id: str`, `url: str` (HTTP base URL like `http://host:8001`), `capacity: int = 4` (max concurrent tasks), `current_load: int = 0` (tasks currently running). Method `is_available(self) -> bool: return self.current_load < self.capacity`
  - `@dataclass class RemoteTaskRequest:` — `task_context_json: str` (serialized `TaskContext.to_json()`), `timeout_s: float = 0.0`
  - `@dataclass class RemoteTaskResponse:` — `worker_result_json: str` (serialized `WorkerResult.to_json()`), `node_id: str = ""`
  - All 3 must be importable from `orchid.remote.types`
  - Verify: `grep -n "class WorkerNode\|class RemoteTaskRequest\|class RemoteTaskResponse\|def is_available" orchid/remote/types.py` must return 4 lines

- [ ] **T267** Create `orchid/remote/worker_server.py`. A FastAPI server that accepts remote task requests and runs them via `SubprocessRunner`. `type:code_generate` `p1` `model:local` `needs:T266`
  - Imports: `import json, os, socket` from stdlib. `from fastapi import FastAPI`. `from orchid.worker_protocol import TaskContext, WorkerResult`. `from orchid.subprocess_runner import SubprocessRunner`. `from orchid.remote.types import RemoteTaskRequest, RemoteTaskResponse`
  - `app = FastAPI(title="Orchid Worker Node")`
  - `NODE_ID: str = os.environ.get("ORCHID_NODE_ID", socket.gethostname())`
  - `_runner = SubprocessRunner()`
  - `GET /health` endpoint: returns `{"status": "ok", "node_id": NODE_ID}`
  - `POST /task` endpoint: body is `RemoteTaskRequest`. Deserializes `TaskContext.from_json(req.task_context_json)`. Calls `_runner.run_task_isolated(ctx, stream_callback=None, timeout_s=req.timeout_s or None)`. Returns `RemoteTaskResponse(worker_result_json=result.to_json(), node_id=NODE_ID)`.
  - `GET /ledger` endpoint: returns the contents of `.orchid/cost_ledger.jsonl` from `ORCHID_PROJECT_DIR` env var. If env var not set or file doesn't exist, returns `{"lines": []}`. Otherwise returns `{"lines": [line for line in path.read_text().splitlines() if line.strip()]}`.
  - Bottom of file: `if __name__ == "__main__": import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("ORCHID_WORKER_PORT", "8001")))`
  - Verify: `grep -n "POST /task\|GET /health\|GET /ledger\|NODE_ID\|_runner" orchid/remote/worker_server.py` must return 5 lines

- [ ] **T268** Create `orchid/remote/dispatcher.py`. One class: `RemoteDispatcher`. `type:code_generate` `p1` `model:local` `needs:T267`
  - Imports: `import json, logging, threading` from stdlib. `import httpx`. `from orchid.worker_protocol import TaskContext, WorkerResult`. `from orchid.remote.types import WorkerNode, RemoteTaskRequest, RemoteTaskResponse`
  - `class RemoteDispatcherError(Exception): pass`
  - `class RemoteDispatcher:` — selects the least-loaded available node and submits tasks via HTTP
  - `__init__(self, nodes: list[WorkerNode]) -> None` — `self._nodes = nodes`, `self._lock = threading.Lock()`
  - `_select_node(self) -> WorkerNode` — acquires lock, finds node where `is_available()` is True with lowest `current_load`. Raises `RemoteDispatcherError("No available worker nodes")` if none available. Returns selected node without releasing lock yet (caller increments load then releases).
  - `dispatch(self, ctx: TaskContext, timeout_s: float = 0.0) -> WorkerResult:` — calls `_select_node()`. Increments `node.current_load` by 1, releases lock. Builds `RemoteTaskRequest(task_context_json=ctx.to_json(), timeout_s=timeout_s)`. POSTs to `f"{node.url}/task"` using `httpx.post(url, json=req.__dict__, timeout=timeout_s + 30 if timeout_s else 300)`. On success, deserializes response as `RemoteTaskResponse`, deserializes `worker_result_json` as `WorkerResult`. In finally, decrements `node.current_load` by 1. Returns WorkerResult. On `httpx.HTTPError as e`: raise `RemoteDispatcherError(str(e))`.
  - `fetch_and_merge_ledger(self, dest_ledger_path: "Path") -> int:` — for each node, GET `{node.url}/ledger`. Parse the `{"lines": [...]}` response. Append each line to `dest_ledger_path` (create if not exists). Return total lines merged across all nodes. On any per-node error, log warning and continue.
  - Verify: `grep -n "class RemoteDispatcher\|class RemoteDispatcherError\|def dispatch\|def _select_node\|def fetch_and_merge_ledger" orchid/remote/dispatcher.py` must return 5 lines

- [ ] **T269** Add `remote` config block to `orchid/orchid.defaults.yaml`. Read the file first. Append at the bottom. `type:code_generate` `p1` `model:local`
  - Append exactly:
    ```yaml
    # T269: Remote worker settings
    remote:
      enabled: false            # true = dispatch task groups to remote worker nodes
      nodes: []                 # list of {node_id: str, url: str, capacity: int} dicts
      merge_ledger_after_group: true  # pull and merge cost ledger from nodes after each group
    ```
  - Verify: `grep -n "remote:" orchid/orchid.defaults.yaml` must return 1 line

- [ ] **T270** Extend `orchid/runner.py` — use `RemoteDispatcher` when `remote.enabled` is true. Read the file first. Find `_execute_group()` method (around line 245). `type:code_generate` `p1` `model:local` `needs:T268,T269`
  - Add import at top: `from orchid.config import cfg`
  - In `_run_loop()`, after `_watchdog.start()` and before the main scheduler while-loop, add:
    ```python
    # T270: Build RemoteDispatcher if remote.enabled
    _remote_dispatcher = None
    if cfg.get("remote.enabled", False):
        from orchid.remote.dispatcher import RemoteDispatcher
        from orchid.remote.types import WorkerNode
        _raw_nodes = cfg.get("remote.nodes", [])
        _nodes = [WorkerNode(**n) for n in _raw_nodes]
        if _nodes:
            _remote_dispatcher = RemoteDispatcher(_nodes)
            logger.info("[runner] Remote dispatch enabled: %d nodes", len(_nodes))
    ```
  - After each parallel group completes (after `_execute_group()` returns), add:
    ```python
    # T270: Merge remote ledger if enabled
    if _remote_dispatcher is not None and cfg.get("remote.merge_ledger_after_group", True):
        try:
            _ledger_path = project_path / ".orchid" / "cost_ledger.jsonl"
            _merged = _remote_dispatcher.fetch_and_merge_ledger(_ledger_path)
            if _merged:
                logger.info("[runner] Merged %d cost ledger lines from remote nodes", _merged)
        except Exception as _re:
            logger.warning("[runner] Remote ledger merge failed: %s", _re)
    ```
  - Verify: `grep -n "remote.enabled\|_remote_dispatcher\|fetch_and_merge_ledger" orchid/runner.py` must return at least 3 lines

- [ ] **T271** Create `orchid/capability.py`. One dataclass and one registry dict. `type:code_generate` `p1` `model:local`
  - Imports: `from dataclasses import dataclass, field`
  - `@dataclass class AgentCapability:` — `agent_type: str`, `allowed_tools: frozenset[str] | None = None` (None = unrestricted), `allowed_file_patterns: list[str] = field(default_factory=list)` (glob patterns like `["src/**", "tests/**"]`; empty = unrestricted), `max_iterations: int = 0` (0 = use config default), `network_access: bool = True`
  - `CAPABILITY_REGISTRY: dict[str, AgentCapability] = {` — define entries for each agent type:
    - `"developer": AgentCapability(agent_type="developer", allowed_tools=None, network_access=True)` — unrestricted
    - `"tester": AgentCapability(agent_type="tester", allowed_tools=frozenset({"bash", "read_file", "list_dir"}), network_access=False)`
    - `"researcher": AgentCapability(agent_type="researcher", allowed_tools=frozenset({"read_file", "list_dir", "bash", "search"}), network_access=True)`
    - `"reviewer": AgentCapability(agent_type="reviewer", allowed_tools=frozenset({"read_file", "list_dir"}), network_access=False)`
    - `"base": AgentCapability(agent_type="base", allowed_tools=None, network_access=True)`
  - `def get_capability(agent_type: str) -> AgentCapability:` — returns `CAPABILITY_REGISTRY.get(agent_type.lower(), CAPABILITY_REGISTRY["base"])`
  - Verify: `grep -n "class AgentCapability\|CAPABILITY_REGISTRY\|def get_capability" orchid/capability.py` must return 3 lines

- [ ] **T272** Extend `orchid/agents/base.py` — read capability from `CAPABILITY_REGISTRY` in `__init__` and use to enforce allowed_tools. Read the file first. Find `__init__()`. `type:code_generate` `p1` `model:local` `needs:T271`
  - In `__init__()`, AFTER the existing `allowed_tools` logic (the block around line 342-359 that reads `_config_allowed`), add:
    ```python
    # T272: Override allowed_tools from AgentCapability registry if capability is stricter
    try:
        from orchid.capability import get_capability
        _cap = get_capability(self.__class__.__name__.lower().replace("agent", ""))
        if _cap.allowed_tools is not None:
            if self._effective_allowed_tools is None:
                self._effective_allowed_tools = _cap.allowed_tools
            else:
                # Intersect: capability further restricts what config already restricted
                self._effective_allowed_tools = self._effective_allowed_tools & _cap.allowed_tools
        if _cap.max_iterations > 0 and self.max_iterations > _cap.max_iterations:
            self.max_iterations = _cap.max_iterations
    except Exception as _cap_err:
        logger.debug("Capability registry lookup failed: %s", _cap_err)
    ```
  - Note: check the actual attribute name for the effective allowed tools set — it may be `self._effective_allowed_tools` or another name. Read the existing code to find it. Use the correct attribute name.
  - Verify: `grep -n "get_capability\|_cap\|CAPABILITY_REGISTRY" orchid/agents/base.py` must return at least 2 lines

- [ ] **T273** Extend `orchid/cost/ledger.py` — add `node_id` field to `TokenRecord` and `merge_from_file()` to `CostLedger`. Read the file first. `type:code_generate` `p1` `model:local`
  - Add `node_id: str = ""` as the LAST field in `@dataclass class TokenRecord:` (after `user_id` added in T258, or after the last existing field)
  - Add this method to `CostLedger` after `merge_from_file` (add it — it doesn't exist yet):
    ```python
    def merge_from_file(self, path: "Path") -> int:
        """Merge TokenRecords from a remote node's JSONL ledger file.

        Returns the number of records merged.
        """
        from pathlib import Path as _Path
        path = _Path(path)
        if not path.exists():
            return 0
        merged = 0
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                record = TokenRecord(**{k: v for k, v in data.items() if k in TokenRecord.__dataclass_fields__})
                with self._lock:
                    self._records.append(record)
                    self._append_to_file(record)
                merged += 1
            except Exception as _e:
                logger.debug("Skipping malformed ledger line: %s", _e)
        return merged
    ```
  - Verify: `grep -n "node_id\|def merge_from_file" orchid/cost/ledger.py` must return at least 2 lines

- [ ] **T274** Extend `orchid/checkpoint/restore.py` — add `export_checkpoint()` function. Read the file first. Add after `list_checkpoints()`. `type:code_generate` `p1` `model:local`
  - Add this function:
    ```python
    def export_checkpoint(
        checkpoint_id: str,
        source_project_dir: "Path",
        dest_dir: "Path",
    ) -> Path:
        """Copy a checkpoint's files to dest_dir for transfer to a remote node.

        Returns the path to the exported checkpoint JSON in dest_dir.
        Raises FileNotFoundError if the checkpoint does not exist.
        """
        import shutil
        from orchid.checkpoint.store import CheckpointStore
        store = CheckpointStore(source_project_dir)
        cp = store.load(checkpoint_id)
        if cp is None:
            raise FileNotFoundError(f"Checkpoint {checkpoint_id!r} not found in {source_project_dir}")
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / f"{checkpoint_id}.json"
        # Re-serialize the checkpoint to the destination
        import json, dataclasses
        dest_file.write_text(json.dumps(dataclasses.asdict(cp)))
        return dest_file
    ```
  - Verify: `grep -n "def export_checkpoint" orchid/checkpoint/restore.py` must return 1 line

- [ ] **T275** Extend `orchid/remote/dispatcher.py` — add task migration: if a node becomes overloaded mid-dispatch, retry on another node. Read the file first. Modify `dispatch()`. `type:code_generate` `p1` `model:local` `needs:T268`
  - Modify `dispatch()` to retry on a different node if the HTTP call fails with `RemoteDispatcherError`:
    - Change `dispatch()` to accept a `max_retries: int = 2` parameter
    - Add a retry loop: try the dispatch, on `RemoteDispatcherError`, decrement `max_retries`, if `max_retries > 0` call `_select_node()` again and retry. If retries exhausted, re-raise.
    - The node's `current_load` must still be decremented in the `finally` of each attempt.
  - Add this method to `RemoteDispatcher`:
    ```python
    def get_least_loaded_node(self) -> WorkerNode | None:
        """Return the node with the lowest current_load, or None if all full."""
        with self._lock:
            available = [n for n in self._nodes if n.is_available()]
            if not available:
                return None
            return min(available, key=lambda n: n.current_load)
    ```
  - Verify: `grep -n "max_retries\|def get_least_loaded_node" orchid/remote/dispatcher.py` must return 2 lines

- [ ] **T276** Create `tests/test_remote_protocol.py`. Write exactly 4 test functions. `type:code_generate` `p2` `model:local` `needs:T266`
  - `test_worker_node_is_available()` — create `WorkerNode(node_id="n1", url="http://x", capacity=4, current_load=0)`, assert `is_available() is True`. Set `current_load=4`, assert `is_available() is False`.
  - `test_worker_node_at_capacity()` — `capacity=2, current_load=3`, assert `is_available() is False`
  - `test_remote_task_request_json_roundtrip()` — create `RemoteTaskRequest(task_context_json='{"task_id":"T001"}', timeout_s=30.0)`, serialize with `json.dumps(dataclasses.asdict(req))`, deserialize, assert `result["timeout_s"] == 30.0`
  - `test_remote_task_response_has_node_id()` — create `RemoteTaskResponse(worker_result_json='{}', node_id="node-1")`, assert `node_id == "node-1"`
  - Import `WorkerNode, RemoteTaskRequest, RemoteTaskResponse` from `orchid.remote.types`
  - Verify: run `python -m pytest tests/test_remote_protocol.py -q` — all 4 must pass

- [ ] **T277** Create `tests/test_remote_dispatcher.py`. Write exactly 3 test functions using `unittest.mock.patch`. `type:code_generate` `p2` `model:local` `needs:T268`
  - `test_dispatch_posts_to_node_url()` — create two `WorkerNode` objects. Patch `httpx.post` to return a mock response with `json()` returning `{"worker_result_json": WorkerResult(task_id="T001", success=True, result="ok", duration_s=1.0).to_json(), "node_id": "n1"}` and `raise_for_status()` as a no-op. Create `RemoteDispatcher([node1, node2])`. Call `dispatch(ctx)`. Assert `httpx.post` was called once with a URL containing `/task`.
  - `test_dispatch_decrements_load_on_success()` — similar mock setup. Before dispatch: `node.current_load == 0`. After dispatch: `node.current_load == 0` again (incremented and then decremented in finally).
  - `test_dispatch_raises_when_no_nodes_available()` — create `RemoteDispatcher([WorkerNode(..., capacity=0)])`. Call `dispatch(ctx)`. Assert raises `RemoteDispatcherError`.
  - Build a dummy `TaskContext` using all-string dummy values.
  - Import `RemoteDispatcher, RemoteDispatcherError` from `orchid.remote.dispatcher`. Import `WorkerNode` from `orchid.remote.types`. Import `TaskContext` from `orchid.worker_protocol`.
  - Verify: run `python -m pytest tests/test_remote_dispatcher.py -q` — all 3 must pass

- [ ] **T278** Create `tests/test_capability.py`. Write exactly 4 test functions. `type:code_generate` `p2` `model:local` `needs:T271`
  - `test_capability_registry_has_all_agent_types()` — import `CAPABILITY_REGISTRY`. Assert all 5 keys exist: `"developer", "tester", "researcher", "reviewer", "base"`.
  - `test_developer_is_unrestricted()` — get `CAPABILITY_REGISTRY["developer"]`. Assert `cap.allowed_tools is None`.
  - `test_reviewer_cannot_bash()` — get `CAPABILITY_REGISTRY["reviewer"]`. Assert `cap.allowed_tools is not None`. Assert `"bash"` not in `cap.allowed_tools`.
  - `test_get_capability_unknown_returns_base()` — call `get_capability("unknown_agent_type")`. Assert `cap.agent_type == "base"`.
  - Import `AgentCapability, CAPABILITY_REGISTRY, get_capability` from `orchid.capability`
  - Verify: run `python -m pytest tests/test_capability.py -q` — all 4 must pass

- [ ] **T279** Create `tests/test_export_checkpoint.py`. Write exactly 2 test functions using `tmp_path`. `type:code_generate` `p2` `model:local` `needs:T274`
  - `test_export_checkpoint_writes_file(tmp_path)` — create a `CheckpointStore(tmp_path)`, save a checkpoint with minimal data (pass empty lists for tasks/decisions/delegations, `hot_memory=""`, `task_id="T001"`). Get the checkpoint_id from the return value. Call `export_checkpoint(checkpoint_id, tmp_path, tmp_path / "export")`. Assert the exported file exists and `json.loads(exported_path.read_text())["metadata"]["task_id"] == "T001"` (or whatever the structure is — read CheckpointStore.save return type first to understand the checkpoint_id and data structure).
  - `test_export_checkpoint_raises_for_missing(tmp_path)` — call `export_checkpoint("NOTEXIST", tmp_path, tmp_path / "export")`. Assert raises `FileNotFoundError`.
  - Import `export_checkpoint` from `orchid.checkpoint.restore`. Import `CheckpointStore` from `orchid.checkpoint.store`.
  - Verify: run `python -m pytest tests/test_export_checkpoint.py -q` — all 2 must pass

- [ ] **T280** Review Tier 4 implementation (T266-T279). Check: remote protocol types are correct, dispatcher selects nodes correctly, capability registry matches existing agent frozensets, export_checkpoint works with real CheckpointStore. `type:review` `p1` `model:claude` `needs:T276,T277,T278,T279`
  - Run `python -c "from orchid.remote.types import WorkerNode, RemoteTaskRequest, RemoteTaskResponse"` — must not error
  - Run `python -c "from orchid.remote.dispatcher import RemoteDispatcher, RemoteDispatcherError"` — must not error
  - Run `python -c "from orchid.capability import CAPABILITY_REGISTRY, get_capability; print(len(CAPABILITY_REGISTRY))"` — must print 5
  - Run `python -c "from orchid.checkpoint.restore import export_checkpoint"` — must not error
  - Run `python -c "from orchid.cost.ledger import CostLedger; print(hasattr(CostLedger, 'merge_from_file'))"` — must print True
  - Run `python -m pytest tests/test_remote_protocol.py tests/test_remote_dispatcher.py tests/test_capability.py tests/test_export_checkpoint.py -q` — all must pass
  - Check that `CAPABILITY_REGISTRY["reviewer"].allowed_tools` is consistent with `ReviewerAgent.allowed_tools` in `orchid/agents/reviewer.py` (they should match or the registry should be stricter)
  - Report PASS or FAIL for each check with the error message if FAIL

- [ ] **T281** Fix all issues found in T280. Read the T280 result first. Make exactly the fixes listed. `type:code_generate` `p1` `model:local` `needs:T280`

- [ ] **T282** Run full test suite and report results. `type:verify` `p1` `model:claude` `needs:T281`
  - Run: `source .venv/bin/activate && python -m pytest tests/ -q --ignore=tests/test_agent_pool.py --ignore=tests/test_parallel_runner.py 2>&1 | tail -20`
  - Report total passed/failed/error counts
  - List any new failures that were not present in Tier 3 (compare against TIER3-REPORT.md)
  - Flag any regressions in existing tests (T000-T208 area)

- [ ] **T283** Fix regressions found in T282. `type:code_generate` `p1` `model:local` `needs:T282`

- [ ] **T284** Rollup Tier 4 results `type:rollup` `rollup:T266,T267,T268,T269,T270,T271,T272,T273,T274,T275,T276,T277,T278,T279,T280,T281,T282,T283` `output:TIER4-REPORT.md` `model:claude`
