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
    # P07 Phase 6: optional tenant identifier for per-tenant quota
    # resolution (isolation.tenant_quotas). "" = no tenant, resolve
    # quotas from the existing global isolation.container_* config.
    tenant_id: str = ""

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
    # P07: additive, container-runner-only diagnostics. Unset ("" / None) for
    # non-container isolation paths (SubprocessRunner) — do not rely on these
    # being present for every WorkerResult.
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    syscall_log_path: str = ""  # P07 Phase 5: gVisor debug-log dir, when syscall tracing is enabled
    # P08 Phase 6: set (to task_id) when a Firecracker task was checkpointed
    # rather than completed -- success=False, but this isn't a normal
    # failure: re-attempting the same task_id will resume from the
    # checkpoint instead of booting fresh. "" for every other outcome.
    checkpoint_id: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))