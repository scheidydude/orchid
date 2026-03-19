"""End-to-end integration smoke tests.

These tests exercise the full path:
    Session.load() → Orchestrator.run_loop() → TaskResultStore

No real LLM calls are made — the provider is mocked at the call() boundary,
which is the narrowest possible seam. Everything else (Session, Orchestrator,
agent dispatch, task routing, TaskResultStore) is real code.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from orchid.memory.state import TaskResultStore, TaskStatus, load_tasks
from orchid.tools.models import RouteDecision


# ── Helpers ────────────────────────────────────────────────────────────────────

_FINAL_ANSWER = "Final Answer: Hello world function written successfully."
_LOCAL_ROUTE = RouteDecision(model="local", reason="test", source="test")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Minimal orchid project: one pending draft task."""
    (tmp_path / "tasks.md").write_text(
        "# Tasks\n\n## TODO\n\n"
        "- [ ] **T001** Write a hello world function `type:draft` `p1`\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# Test Project\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def two_task_project(tmp_path: Path) -> Path:
    """Two independent pending tasks."""
    (tmp_path / "tasks.md").write_text(
        "# Tasks\n\n## TODO\n\n"
        "- [ ] **T001** First task `type:draft` `p1`\n"
        "- [ ] **T002** Second task `type:draft` `p2`\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# Test Project\n", encoding="utf-8")
    return tmp_path


def _run(project: Path, max_tasks: int = 10) -> None:
    """Load session, run loop, save. Core helper shared across tests."""
    from orchid.session import Session
    from orchid.orchestrator import Orchestrator

    session = Session(project_dir=project)
    session.load()

    with patch("orchid.tools.models.call", return_value=_FINAL_ANSWER), \
         patch("orchid.tools.models.route", return_value=_LOCAL_ROUTE):
        orch = Orchestrator(session)
        orch.run_loop(max_tasks=max_tasks)

    session.save()


# ── Session.load ───────────────────────────────────────────────────────────────


def test_session_load_parses_tasks(project: Path) -> None:
    from orchid.session import Session

    session = Session(project_dir=project)
    session.load()

    assert len(session.tasks) == 1
    assert session.tasks[0].id == "T001"
    assert session.tasks[0].status == TaskStatus.TODO


def test_session_load_reads_hot_memory(project: Path) -> None:
    from orchid.session import Session

    session = Session(project_dir=project)
    session.load()

    assert "Test Project" in session.hot_memory


# ── Orchestrator.run_loop ──────────────────────────────────────────────────────


def test_run_loop_marks_task_done(project: Path) -> None:
    _run(project)

    tasks = load_tasks(project)
    assert tasks[0].id == "T001"
    assert tasks[0].status == TaskStatus.DONE


def test_run_loop_persists_to_tasks_md(project: Path) -> None:
    """tasks.md on disk should reflect DONE status after run_loop + save."""
    _run(project)

    raw = (project / "tasks.md").read_text(encoding="utf-8")
    assert "[x]" in raw


def test_run_loop_stores_result(project: Path) -> None:
    """TaskResultStore should have an entry for the completed task."""
    _run(project)

    store = TaskResultStore(project)
    entry = store.get("T001")
    assert entry is not None
    assert entry["task_id"] == "T001"
    assert entry["result"]


def test_run_loop_result_contains_answer(project: Path) -> None:
    """The stored result should contain the text from our fake Final Answer."""
    _run(project)

    store = TaskResultStore(project)
    entry = store.get("T001")
    assert "Hello world function written successfully" in entry["result"]


def test_run_loop_processes_multiple_tasks(two_task_project: Path) -> None:
    _run(two_task_project)

    tasks = load_tasks(two_task_project)
    statuses = {t.id: t.status for t in tasks}
    assert statuses["T001"] == TaskStatus.DONE
    assert statuses["T002"] == TaskStatus.DONE

    store = TaskResultStore(two_task_project)
    assert store.get("T001") is not None
    assert store.get("T002") is not None


def test_run_loop_no_tasks_is_noop(tmp_path: Path) -> None:
    """Empty task board should not raise."""
    (tmp_path / "tasks.md").write_text("# Tasks\n\n## TODO\n\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Empty project\n", encoding="utf-8")

    from orchid.session import Session
    from orchid.orchestrator import Orchestrator

    session = Session(project_dir=tmp_path)
    session.load()

    with patch("orchid.tools.models.call", return_value=_FINAL_ANSWER), \
         patch("orchid.tools.models.route", return_value=_LOCAL_ROUTE):
        orch = Orchestrator(session)
        orch.run_loop(max_tasks=10)  # should return immediately

    assert load_tasks(tmp_path) == []


# ── Session.save writes task status ───────────────────────────────────────────


def test_session_save_persists_done_status(project: Path) -> None:
    """session.save() should write DONE status back to tasks.md."""
    from orchid.session import Session
    from orchid.orchestrator import Orchestrator

    session = Session(project_dir=project)
    session.load()

    with patch("orchid.tools.models.call", return_value=_FINAL_ANSWER), \
         patch("orchid.tools.models.route", return_value=_LOCAL_ROUTE):
        Orchestrator(session).run_loop(max_tasks=5)

    # Verify in-memory state before save
    assert session.tasks[0].status == TaskStatus.DONE

    # run_loop calls session.save() internally — reload from disk to confirm
    reloaded = load_tasks(project)
    assert reloaded[0].status == TaskStatus.DONE
