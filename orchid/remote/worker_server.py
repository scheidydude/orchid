import os
import socket
from pathlib import Path

from fastapi import FastAPI

from orchid.remote.types import RemoteTaskRequest, RemoteTaskResponse
from orchid.subprocess_runner import SubprocessRunner
from orchid.worker_protocol import TaskContext

app = FastAPI(title="Orchid Worker Node")

NODE_ID: str = os.environ.get("ORCHID_NODE_ID", socket.gethostname())

_runner = SubprocessRunner()


@app.get("/health")
def health():
    return {"status": "ok", "node_id": NODE_ID}


@app.post("/task")
def handle_task(req: RemoteTaskRequest):
    ctx = TaskContext.from_json(req.task_context_json)
    result = _runner.run_task_isolated(ctx, stream_callback=None, timeout_s=req.timeout_s or None)
    return RemoteTaskResponse(worker_result_json=result.to_json(), node_id=NODE_ID)


@app.get("/ledger")
def get_ledger():
    project_dir = os.environ.get("ORCHID_PROJECT_DIR")
    if not project_dir:
        return {"lines": []}
    ledger_path = Path(project_dir) / ".orchid" / "cost_ledger.jsonl"
    if not ledger_path.exists():
        return {"lines": []}
    lines = [line for line in ledger_path.read_text().splitlines() if line.strip()]
    return {"lines": lines}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("ORCHID_WORKER_PORT", "8001")))