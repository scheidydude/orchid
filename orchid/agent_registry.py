"""Global registry mapping task_id → running agent instance.

Allows runner/endpoints to reach into a live agent for suspend/resume
without coupling the orchestrator to the runner.
"""

from __future__ import annotations

import threading
from typing import Any

_registry: dict[str, Any] = {}   # task_id → BaseAgent
_lock = threading.Lock()


def register(task_id: str, agent: Any) -> None:
    with _lock:
        _registry[task_id] = agent


def deregister(task_id: str) -> None:
    with _lock:
        _registry.pop(task_id, None)


def get(task_id: str) -> Any | None:
    with _lock:
        return _registry.get(task_id)


def all_task_ids() -> list[str]:
    with _lock:
        return list(_registry.keys())
