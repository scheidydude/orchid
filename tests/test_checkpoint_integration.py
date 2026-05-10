"""Integration test for the full checkpoint save → restore pipeline."""

from pathlib import Path

from orchid.checkpoint.restore import rewind_session
from orchid.checkpoint.store import CheckpointStore


def test_checkpoint_save_and_restore_full_pipeline(tmp_path: Path) -> None:
    """Full pipeline: save checkpoint during a simulated task, then rewind the session.

    Verifies that after rewind_session the in-memory session state matches the
    checkpoint (tasks, hot_memory, decisions, cache_stats), and that tasks added
    after the checkpoint are not present in the rewound session.
    """
    # Minimal project structure required by Session.load()
    (tmp_path / "tasks.md").write_text("# Tasks\n", encoding="utf-8")

    store = CheckpointStore(tmp_path)

    # Simulate state just before T002 begins
    checkpoint_tasks = [
        {"id": "T001", "title": "Design schema", "status": "DONE"},
        {"id": "T002", "title": "Implement API", "status": "TODO"},
    ]
    cp = store.save(
        tasks=checkpoint_tasks,
        hot_memory="schema design approved, using SQLite",
        decisions=[{"id": "D001", "title": "Use SQLite"}],
        delegations=[],
        extra_context="project context",
        cache_stats={"cache_hits": 10, "cache_writes": 4},
        task_id="T002",
        description="Before T002",
    )

    # Simulate post-checkpoint tasks.md with a new task added after the checkpoint
    (tmp_path / "tasks.md").write_text(
        "# Tasks\n\n"
        "- [ ] **T001** Design schema\n"
        "- [ ] **T002** Implement API\n"
        "- [ ] **T003** Write tests\n",
        encoding="utf-8",
    )

    # Rewind to the checkpoint
    result = rewind_session(tmp_path, cp.metadata.checkpoint_id)

    assert result is not None
    assert result.metadata.checkpoint_id == cp.metadata.checkpoint_id
    assert result.metadata.task_id == "T002"

    # Checkpoint state is restored correctly
    assert result.hot_memory == "schema design approved, using SQLite"
    assert result.decisions == [{"id": "D001", "title": "Use SQLite"}]
    assert result.cache_stats == {"cache_hits": 10, "cache_writes": 4}

    # Only tasks present at checkpoint time are kept (T003 added after must not appear)
    task_ids = {t["id"] for t in result.tasks}
    assert "T001" in task_ids
    assert "T002" in task_ids
    assert "T003" not in task_ids
