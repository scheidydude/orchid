import json
import time
from dataclasses import dataclass, field


def _ts() -> float:
    return time.time()


@dataclass
class SessionStartEvent:
    type: str = "session_start"
    session_id: str = ""
    project: str = ""
    mode: str = ""
    ts: float = field(default_factory=_ts)

    def to_json(self) -> str:
        return json.dumps(self.__dict__)


@dataclass
class TaskStartEvent:
    type: str = "task_start"
    session_id: str = ""
    task_id: str = ""
    task_title: str = ""
    task_type: str = ""
    ts: float = field(default_factory=_ts)

    def to_json(self) -> str:
        return json.dumps(self.__dict__)


@dataclass
class AgentThoughtEvent:
    type: str = "agent_thought"
    session_id: str = ""
    task_id: str = ""
    thought: str = ""
    ts: float = field(default_factory=_ts)

    def to_json(self) -> str:
        return json.dumps(self.__dict__)


@dataclass
class ToolUseEvent:
    type: str = "tool_use"
    session_id: str = ""
    task_id: str = ""
    tool: str = ""
    input_summary: str = ""
    ts: float = field(default_factory=_ts)

    def to_json(self) -> str:
        return json.dumps(self.__dict__)


@dataclass
class ToolResultEvent:
    type: str = "tool_result"
    session_id: str = ""
    task_id: str = ""
    tool: str = ""
    output_summary: str = ""
    ts: float = field(default_factory=_ts)

    def to_json(self) -> str:
        return json.dumps(self.__dict__)


@dataclass
class TaskCompleteEvent:
    type: str = "task_complete"
    session_id: str = ""
    task_id: str = ""
    duration_s: float = 0.0
    iterations: int = 0
    ts: float = field(default_factory=_ts)

    def to_json(self) -> str:
        return json.dumps(self.__dict__)


@dataclass
class TaskFailEvent:
    type: str = "task_fail"
    session_id: str = ""
    task_id: str = ""
    error: str = ""
    ts: float = field(default_factory=_ts)

    def to_json(self) -> str:
        return json.dumps(self.__dict__)


@dataclass
class SessionEndEvent:
    type: str = "session_end"
    session_id: str = ""
    task_count: int = 0
    duration_s: float = 0.0
    ts: float = field(default_factory=_ts)

    def to_json(self) -> str:
        return json.dumps(self.__dict__)
