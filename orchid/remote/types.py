from dataclasses import dataclass


@dataclass
class WorkerNode:
    node_id: str
    url: str
    capacity: int = 4
    current_load: int = 0

    def is_available(self) -> bool:
        return self.current_load < self.capacity


@dataclass
class RemoteTaskRequest:
    task_context_json: str
    timeout_s: float = 0.0


@dataclass
class RemoteTaskResponse:
    worker_result_json: str
    node_id: str = ""