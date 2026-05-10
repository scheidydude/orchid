"""End-to-end integration smoke tests and _tracking_write manifest tests.

These tests exercise the full path:
    Session.load() → Orchestrator.run_loop() → TaskResultStore

No real LLM calls are made — call() is mocked at the use-site in both
orchestrator.py and agents/base.py (both do `from orchid.tools.models import call`,
so patching orchid.tools.models.call would only intercept calls made after
module import, which is unreliable once modules are cached in the full suite).
Everything else (Session, Orchestrator, agent dispatch, routing, TaskResultStore)
is real code.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from orchid.memory.state import TaskResultStore, TaskStatus, load_tasks

# ── Helpers ────────────────────────────────────────────────────────────────────

_FINAL_ANSWER = "Final Answer: Hello world function written successfully."


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Minimal orchid project: one pending draft task."""
    (tmp_path / "tasks.md").write_text(
        "# Tasks\n\n## TODO\n\n- [ ] **T001** Write a hello world function `type:draft` `p1`\n",
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
    from orchid.orchestrator import Orchestrator
    from orchid.session import Session

    session = Session(project_dir=project)
    session.load()

    with (
        patch("orchid.orchestrator.call", return_value=_FINAL_ANSWER),
        patch("orchid.agents.base.call", return_value=_FINAL_ANSWER),
        patch("orchid.providers.registry.ProviderRegistry.resolve_name", return_value="local"),
    ):
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

    from orchid.orchestrator import Orchestrator
    from orchid.session import Session

    session = Session(project_dir=tmp_path)
    session.load()

    with (
        patch("orchid.orchestrator.call", return_value=_FINAL_ANSWER),
        patch("orchid.agents.base.call", return_value=_FINAL_ANSWER),
        patch("orchid.providers.registry.ProviderRegistry.resolve_name", return_value="local"),
    ):
        orch = Orchestrator(session)
        orch.run_loop(max_tasks=10)  # should return immediately

    assert load_tasks(tmp_path) == []


# ── Session.save writes task status ───────────────────────────────────────────


def test_session_save_persists_done_status(project: Path) -> None:
    """session.save() should write DONE status back to tasks.md."""
    from orchid.orchestrator import Orchestrator
    from orchid.session import Session

    session = Session(project_dir=project)
    session.load()

    with (
        patch("orchid.orchestrator.call", return_value=_FINAL_ANSWER),
        patch("orchid.agents.base.call", return_value=_FINAL_ANSWER),
        patch("orchid.providers.registry.ProviderRegistry.resolve_name", return_value="local"),
    ):
        Orchestrator(session).run_loop(max_tasks=5)

    # Verify in-memory state before save
    assert session.tasks[0].status == TaskStatus.DONE

    # run_loop calls session.save() internally — reload from disk to confirm
    reloaded = load_tasks(project)
    assert reloaded[0].status == TaskStatus.DONE


# ── _tracking_write manifest recording ────────────────────────────────────────


_WRITE_RESP = (
    "Thought: I will write the file.\n"
    "Action: write_file\n"
    "Action Path: output.txt\n"
    "Action Content:\n"
    "<<<ORCHID\n"
    "hello world\n"
    "ORCHID"
)


def test_tracking_write_records_file_in_manifest(tmp_path: Path) -> None:
    """Files written via write_file are recorded in a task_manifest session event."""
    (tmp_path / "tasks.md").write_text(
        "# Tasks\n\n## TODO\n\n- [ ] **T001** Write hello world `type:code_generate` `p1`\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# Test\n", encoding="utf-8")

    from orchid.orchestrator import Orchestrator
    from orchid.session import Session

    session = Session(project_dir=tmp_path)
    session.load()

    with (
        patch("orchid.orchestrator.call", return_value=_FINAL_ANSWER),
        patch("orchid.agents.base.call", side_effect=[_WRITE_RESP, _FINAL_ANSWER]),
        patch("orchid.providers.registry.ProviderRegistry.resolve_name", return_value="local"),
    ):
        Orchestrator(session).run_loop(max_tasks=1)

    log_dir = tmp_path / ".orchid" / "session_logs"
    log_files = list(log_dir.glob("*.jsonl"))
    assert log_files, "No session log written"

    events = [json.loads(line) for line in log_files[0].read_text(encoding="utf-8").splitlines() if line]
    manifest_events = [e for e in events if e.get("type") == "task_manifest"]
    assert len(manifest_events) == 1, f"Expected 1 manifest event, got: {manifest_events}"
    assert manifest_events[0]["task_id"] == "T001"
    assert any("output.txt" in p for p in manifest_events[0]["files_created"])


def test_tracking_write_missing_content_returns_error(tmp_path: Path) -> None:
    """_tracking_write with no content returns a helpful error instead of raising TypeError."""
    (tmp_path / "tasks.md").write_text(
        "# Tasks\n\n## TODO\n\n- [ ] **T001** Write file `type:code_generate` `p1`\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# Test\n", encoding="utf-8")

    # Model uses "Action Path:" without heredoc block — dispatches write_file with path only
    _PATH_ONLY_RESP = "Thought: Writing now.\nAction: write_file\nAction Path: output.txt"

    from orchid.orchestrator import Orchestrator
    from orchid.session import Session

    session = Session(project_dir=tmp_path)
    session.load()

    with (
        patch("orchid.orchestrator.call", return_value=_FINAL_ANSWER),
        patch("orchid.agents.base.call", side_effect=[_PATH_ONLY_RESP, _FINAL_ANSWER]),
        patch("orchid.providers.registry.ProviderRegistry.resolve_name", return_value="local"),
    ):
        # Should complete without raising TypeError
        Orchestrator(session).run_loop(max_tasks=1)

    # No manifest event: no file was actually written
    log_dir = tmp_path / ".orchid" / "session_logs"
    log_files = list(log_dir.glob("*.jsonl"))
    assert log_files
    events = [json.loads(line) for line in log_files[0].read_text(encoding="utf-8").splitlines() if line]
    manifest_events = [e for e in events if e.get("type") == "task_manifest"]
    assert manifest_events == [], "No manifest should be logged when content was missing"
