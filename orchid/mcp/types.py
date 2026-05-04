from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPTool:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPResult:
    content: str
    isError: bool = False


@dataclass
class MCPError:
    message: str
    code: int = -1