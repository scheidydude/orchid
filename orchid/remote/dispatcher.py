import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from orchid.remote.types import RemoteTaskRequest, RemoteTaskResponse, WorkerNode
from orchid.worker_protocol import TaskContext, WorkerResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class RemoteDispatcherError(Exception):
    pass


class RemoteDispatcher:
    def __init__(self, nodes: list[WorkerNode]) -> None:
        self._nodes = nodes
        self._lock = threading.Lock()

    def _select_node(self) -> WorkerNode:
        """Select the least-loaded available node.

        Returns the selected node WITHOUT releasing the lock.
        The caller is responsible for incrementing the load and then releasing the lock.
        Raises RemoteDispatcherError if no node is available.
        """
        best: WorkerNode | None = None
        best_load = float("inf")
        for node in self._nodes:
            if node.is_available() and node.current_load < best_load:
                best = node
                best_load = node.current_load
        if best is None:
            raise RemoteDispatcherError("No available worker nodes")
        return best

    def get_least_loaded_node(self) -> WorkerNode | None:
        """Return the node with the lowest current_load, or None if all full."""
        with self._lock:
            available = [n for n in self._nodes if n.is_available()]
            if not available:
                return None
            return min(available, key=lambda n: n.current_load)

    def dispatch(self, ctx: TaskContext, timeout_s: float = 0.0, max_retries: int = 2) -> WorkerResult:
        """Dispatch a task to the least-loaded available worker node."""
        retries = max_retries
        while True:
            node = self._select_node()
            node.current_load += 1
            try:
                req = RemoteTaskRequest(
                    task_context_json=ctx.to_json(),
                    timeout_s=timeout_s,
                )
                http_timeout = timeout_s + 30 if timeout_s else 300
                resp = httpx.post(
                    f"{node.url}/task",
                    json=req.__dict__,
                    timeout=http_timeout,
                )
                resp.raise_for_status()
                response = RemoteTaskResponse(**resp.json())
                return WorkerResult(**json.loads(response.worker_result_json))
            except httpx.HTTPError as e:
                raise RemoteDispatcherError(str(e))
            except RemoteDispatcherError:
                if retries > 0:
                    retries -= 1
                    continue
                raise
            finally:
                node.current_load -= 1

    def fetch_and_merge_ledger(self, dest_ledger_path: Path) -> int:
        """Fetch ledger lines from every worker node and append them to dest_ledger_path.

        Returns the total number of lines merged across all nodes.
        On any per-node error, logs a warning and continues.
        """
        dest = Path(dest_ledger_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        for node in self._nodes:
            try:
                resp = httpx.get(f"{node.url}/ledger", timeout=30)
                resp.raise_for_status()
                data = resp.json()
                lines = data.get("lines", [])
                if lines:
                    with open(dest, "a") as f:
                        for line in lines:
                            f.write(str(line) + "\n")
                    total += len(lines)
            except Exception as e:
                logger.warning("Failed to fetch ledger from node %s: %s", node.node_id, e)
        return total