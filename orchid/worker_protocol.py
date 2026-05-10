import json
from dataclasses import asdict, dataclass, field


@dataclass
class TaskContext:
    task_id: str
    task_description: str
    session_context: str
    agent_type: str
    model_key: str
    project_dir: str
    injection_queue_path: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "TaskContext":
        return cls(**json.loads(s))


@dataclass
class WorkerEvent:
    type: str
    task_id: str
    payload: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "task_id": self.task_id, **self.payload})


@dataclass
class WorkerResult:
    task_id: str
    success: bool
    result: str = ""
    error: str = ""
    duration_s: float = 0.0
    cpu_seconds: float = 0.0  # Phase 6: child CPU time (user + sys)

    def to_json(self) -> str:
        return json.dumps(asdict(self))