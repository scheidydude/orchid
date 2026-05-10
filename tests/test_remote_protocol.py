import json
from dataclasses import asdict

from orchid.remote.types import RemoteTaskRequest, RemoteTaskResponse, WorkerNode


def test_worker_node_is_available():
    node = WorkerNode(node_id="n1", url="http://x", capacity=4, current_load=0)
    assert node.is_available() is True
    node.current_load = 4
    assert node.is_available() is False


def test_worker_node_at_capacity():
    node = WorkerNode(node_id="n1", url="http://x", capacity=2, current_load=3)
    assert node.is_available() is False


def test_remote_task_request_json_roundtrip():
    req = RemoteTaskRequest(task_context_json='{"task_id":"T001"}', timeout_s=30.0)
    data = json.dumps(asdict(req))
    result = json.loads(data)
    assert result["timeout_s"] == 30.0


def test_remote_task_response_has_node_id():
    resp = RemoteTaskResponse(worker_result_json='{}', node_id="node-1")
    assert resp.node_id == "node-1"
