from dataclasses import dataclass, field


@dataclass
class AgentCapability:
    agent_type: str
    allowed_tools: frozenset[str] | None = None  # None = unrestricted
    allowed_file_patterns: list[str] = field(default_factory=list)  # glob patterns; empty = unrestricted
    max_iterations: int = 0  # 0 = use config default
    network_access: bool = True


CAPABILITY_REGISTRY: dict[str, AgentCapability] = {
    "developer": AgentCapability(
        agent_type="developer",
        allowed_tools=None,
        network_access=True,
    ),
    "tester": AgentCapability(
        agent_type="tester",
        allowed_tools=frozenset({
            "read_file", "list_dir", "bash", "check_imports", "get_task_files",
        }),
        network_access=False,
    ),
    "researcher": AgentCapability(
        agent_type="researcher",
        allowed_tools=frozenset({
            "read_file", "list_dir", "bash", "search", "fetch", "get_task_files",
        }),
        network_access=True,
    ),
    "reviewer": AgentCapability(
        agent_type="reviewer",
        allowed_tools=frozenset({
            "read_file", "list_dir", "bash", "check_imports", "get_task_files",
        }),
        network_access=False,
    ),
    "base": AgentCapability(
        agent_type="base",
        allowed_tools=None,
        network_access=True,
    ),
}


def get_capability(agent_type: str) -> AgentCapability:
    return CAPABILITY_REGISTRY.get(agent_type.lower(), CAPABILITY_REGISTRY["base"])