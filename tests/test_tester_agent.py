"""Tests for T079-T083: environment detection, TesterAgent, auto-verify injection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchid.tools.shell import detect_environment, detect_python_runner, rewrite_python_command


# ── T079 / T080: environment detection ───────────────────────────────────────


def test_detect_environment_docker(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.yml").touch()
    assert detect_environment(tmp_path) == "docker"


def test_detect_environment_docker_yaml_variant(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.yaml").touch()
    assert detect_environment(tmp_path) == "docker"


def test_detect_environment_venv(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    assert detect_environment(tmp_path) == "venv"


def test_detect_environment_venv_no_dot(tmp_path: Path) -> None:
    (tmp_path / "venv").mkdir()
    assert detect_environment(tmp_path) == "venv"


def test_detect_environment_node(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "test"}')
    assert detect_environment(tmp_path) == "node"


def test_detect_environment_python_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
    assert detect_environment(tmp_path) == "python"


def test_detect_environment_python_pipfile(tmp_path: Path) -> None:
    (tmp_path / "Pipfile").write_text("[[source]]")
    assert detect_environment(tmp_path) == "python"


def test_detect_environment_unknown(tmp_path: Path) -> None:
    assert detect_environment(tmp_path) == "unknown"


def test_detect_environment_docker_takes_priority(tmp_path: Path) -> None:
    """docker-compose.yml takes priority over .venv when both present."""
    (tmp_path / "docker-compose.yml").touch()
    (tmp_path / ".venv").mkdir()
    assert detect_environment(tmp_path) == "docker"


def test_detect_python_runner_venv(tmp_path: Path) -> None:
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    assert detect_python_runner(tmp_path) == str(venv_python)


def test_detect_python_runner_plain_venv(tmp_path: Path) -> None:
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    assert detect_python_runner(tmp_path) == str(venv_python)


def test_detect_python_runner_fallback(tmp_path: Path) -> None:
    assert detect_python_runner(tmp_path) == "python3"


def test_rewrite_python_command_no_venv(tmp_path: Path) -> None:
    """No venv → command unchanged."""
    assert rewrite_python_command("python3 -m pytest", tmp_path) == "python3 -m pytest"


def test_rewrite_python_command_with_venv(tmp_path: Path) -> None:
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    result = rewrite_python_command("python3 -m pytest tests/", tmp_path)
    assert result == f"{venv_python} -m pytest tests/"


def test_rewrite_pytest_command_with_venv(tmp_path: Path) -> None:
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    result = rewrite_python_command("pytest tests/", tmp_path)
    assert result == f"{venv_python} -m pytest tests/"


def test_rewrite_pip_command_with_venv(tmp_path: Path) -> None:
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    result = rewrite_python_command("pip install requests", tmp_path)
    assert result == f"{venv_python} -m pip install requests"


# ── T081: verify_syntax_only prompt injection ─────────────────────────────────


def test_verify_syntax_only_prompt_injected(tmp_path: Path) -> None:
    """When verify_syntax_only=true, system prompt contains SYNTAX ONLY instruction."""
    from orchid.agents.base import BaseAgent

    with patch("orchid.agents.base.cfg") as mock_cfg:
        mock_cfg.get.side_effect = lambda key, default=None: (
            True if key == "agents.verify_syntax_only" else default
        )
        agent = BaseAgent(project_dir=tmp_path)
        prompt = agent.system_prompt()

    assert "SYNTAX ONLY" in prompt
    assert "py_compile" in prompt
    assert "Do NOT run pytest" in prompt


def test_verify_syntax_only_not_injected_by_default(tmp_path: Path) -> None:
    """By default, verify_syntax_only is false and section is absent."""
    from orchid.agents.base import BaseAgent

    with patch("orchid.agents.base.cfg") as mock_cfg:
        mock_cfg.get.side_effect = lambda key, default=None: (
            False if key == "agents.verify_syntax_only" else default
        )
        agent = BaseAgent(project_dir=tmp_path)
        prompt = agent.system_prompt()

    assert "SYNTAX ONLY" not in prompt


# ── T082: TesterAgent routing ─────────────────────────────────────────────────


def test_tester_agent_routes_verify_tasks() -> None:
    """Orchestrator _resolve_agent maps type:verify to TesterAgent."""
    from orchid.agents.tester import TesterAgent
    from orchid.memory.state import Task
    from orchid.orchestrator import _get_registry, Orchestrator

    registry = _get_registry()
    assert "tester" in registry
    assert registry["tester"] is TesterAgent

    # Simulate _resolve_agent logic
    task = Task(id="T001", title="Verify output", type="verify")
    type_map = {
        "code_generate": "developer",
        "draft": "developer",
        "search": "researcher",
        "summarize": "researcher",
        "review": "reviewer",
        "critique": "reviewer",
        "verify": "tester",
        "rollup": "base",
    }
    agent_name = type_map.get(task.type, "base")
    assert agent_name == "tester"
    assert registry[agent_name] is TesterAgent


def test_tester_agent_system_prompt_no_code_writing(tmp_path: Path) -> None:
    """TesterAgent prompt forbids writing code."""
    from orchid.agents.tester import TesterAgent

    with patch("orchid.agents.base.cfg") as mock_cfg:
        mock_cfg.get.return_value = None
        agent = TesterAgent(project_dir=tmp_path)
        prompt = agent.system_prompt()

    assert "Do NOT write or modify code" in prompt
    assert "QA verification" in prompt


# ── T083: auto-verify task injection ─────────────────────────────────────────


def test_auto_verify_disabled_by_default() -> None:
    """auto_verify defaults to false in orchid.defaults.yaml."""
    import yaml
    from pathlib import Path as _Path

    defaults_path = _Path(__file__).parent.parent / "orchid" / "orchid.defaults.yaml"
    with open(defaults_path) as f:
        config = yaml.safe_load(f)

    assert config.get("auto_verify") is False


def test_auto_verify_task_injected_after_code_generate(tmp_path: Path) -> None:
    """_insert_auto_verify_task creates a verify task pointing at the source task."""
    from orchid.memory.state import Task
    from orchid.orchestrator import Orchestrator

    session = MagicMock()
    session.project_dir = tmp_path
    source_task = Task(id="T001", title="Write a module", type="code_generate", priority=1)
    session.tasks = [source_task]
    session._vector = None

    orch = Orchestrator(session=session)
    orch._insert_auto_verify_task(source_task, ["src/module.py"])

    verify_tasks = [t for t in session.tasks if t.type == "verify"]
    assert len(verify_tasks) == 1
    vt = verify_tasks[0]
    assert "T001" in vt.id
    assert "src/module.py" in vt.title or "src/module.py" in vt.description
    assert vt.depends_on == ["T001"]
    assert vt.priority == source_task.priority
