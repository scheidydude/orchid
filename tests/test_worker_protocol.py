import json

from orchid.worker_protocol import TaskContext, WorkerEvent, WorkerResult


def test_taskcontext_to_json_and_from_json():
    original = TaskContext(
        task_id="T001",
        task_description="do stuff",
        session_context="session-abc",
        agent_type="base",
        model_key="local",
        project_dir="/tmp/proj",
        injection_queue_path="/tmp/queue",
    )
    j = original.to_json()
    restored = TaskContext.from_json(j)
    assert restored.task_id == original.task_id
    assert restored.task_description == original.task_description
    assert restored.session_context == original.session_context
    assert restored.agent_type == original.agent_type
    assert restored.model_key == original.model_key
    assert restored.project_dir == original.project_dir
    assert restored.injection_queue_path == original.injection_queue_path


def test_workerevent_to_json_includes_payload_fields():
    event = WorkerEvent(type="agent_step", task_id="T001", payload={"thought": "hello"})
    j = event.to_json()
    result = json.loads(j)
    assert result["type"] == "agent_step"
    assert result["thought"] == "hello"


def test_workerresult_defaults():
    result = WorkerResult(task_id="T001", success=True)
    assert result.result == ""
    assert result.error == ""
    assert result.duration_s == 0.0


def test_workerresult_to_json_roundtrip():
    r = WorkerResult(task_id="T002", success=False, error="oops", duration_s=1.5)
    data = json.loads(r.to_json())
    assert data["success"] is False
    assert data["error"] == "oops"