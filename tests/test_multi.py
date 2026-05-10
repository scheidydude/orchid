"""Tests for multi-project parallelism — orchid/multi.py and multi_formatter.py."""

from __future__ import annotations

import multiprocessing
from unittest.mock import MagicMock, patch

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_task(task_id: str, status: str = "TODO", title: str = "Test task") -> MagicMock:
    t = MagicMock()
    t.id = task_id
    t.title = title
    t.status.value = status
    return t


class _SyncQueue:
    """Simple thread-safe list masquerading as a Queue — avoids multiprocessing.Queue
    feeder-thread timing issues when testing in a single process."""

    def __init__(self):
        import threading
        self._items: list = []
        self._lock = threading.Lock()

    def put_nowait(self, item):
        with self._lock:
            self._items.append(item)

    def empty(self):
        with self._lock:
            return len(self._items) == 0

    def get_nowait(self):
        with self._lock:
            if not self._items:
                raise Exception("empty")
            return self._items.pop(0)

    def drain(self) -> list:
        with self._lock:
            items, self._items = self._items, []
            return items


# ── worker_main ────────────────────────────────────────────────────────────────



def test_worker_main_runs_project(tmp_path):
    """worker_main loads a Session, drains tasks, sends notifications."""
    from orchid.memory.state import TaskStatus

    task = _make_task("T001", "TODO")
    task.status = TaskStatus.TODO

    mock_session = MagicMock()
    mock_session.tasks = [task]
    mock_session.next_task.side_effect = [task, None]  # one task then done
    mock_session.project_name = tmp_path.name

    mock_orch = MagicMock()
    mock_orch._execute_task.return_value = {"status": "done", "result": "OK"}

    queue = _SyncQueue()
    semaphore = multiprocessing.Semaphore(3)
    local_semaphore = multiprocessing.Semaphore(1)
    stop_event = multiprocessing.Event()

    with (
        patch("orchid.multi._install_semaphore_wrapper"),
        patch("orchid.session.Session", return_value=mock_session),
        patch("orchid.orchestrator.Orchestrator", return_value=mock_orch),
    ):
        from orchid.multi import worker_main
        worker_main(str(tmp_path), queue, semaphore, local_semaphore, stop_event, code_model=None)

    events = queue.drain()
    event_types = [e["event"] for e in events]
    assert "session_start" in event_types
    assert "task_start" in event_types
    assert "task_complete" in event_types
    assert "session_complete" in event_types

    for e in events:
        assert e["project"] == tmp_path.name


def test_stop_event_halts_worker(tmp_path):
    """worker_main exits immediately when stop_event is pre-set."""
    from orchid.memory.state import TaskStatus

    task = _make_task("T001", "TODO")
    task.status = TaskStatus.TODO

    mock_session = MagicMock()
    mock_session.tasks = [task]
    mock_session.next_task.return_value = task

    mock_orch = MagicMock()

    queue = _SyncQueue()
    semaphore = multiprocessing.Semaphore(3)
    local_semaphore = multiprocessing.Semaphore(1)
    stop_event = multiprocessing.Event()
    stop_event.set()  # pre-set stop

    with (
        patch("orchid.multi._install_semaphore_wrapper"),
        patch("orchid.session.Session", return_value=mock_session),
        patch("orchid.orchestrator.Orchestrator", return_value=mock_orch),
    ):
        from orchid.multi import worker_main
        worker_main(str(tmp_path), queue, semaphore, local_semaphore, stop_event, code_model=None)

    # next_task should not have been called (stop_event was set before any task loop)
    mock_session.next_task.assert_not_called()


def test_notification_queue_receives_events(tmp_path):
    """worker_main sends structured events to the notification queue."""
    from orchid.memory.state import TaskStatus

    task = _make_task("T001", "TODO")
    task.status = TaskStatus.TODO

    mock_session = MagicMock()
    mock_session.tasks = [task]
    mock_session.next_task.side_effect = [task, None]
    mock_session.project_name = tmp_path.name

    mock_orch = MagicMock()
    mock_orch._execute_task.return_value = {"status": "done", "result": "success"}

    queue = _SyncQueue()
    semaphore = multiprocessing.Semaphore(3)
    local_semaphore = multiprocessing.Semaphore(1)
    stop_event = multiprocessing.Event()

    with (
        patch("orchid.multi._install_semaphore_wrapper"),
        patch("orchid.session.Session", return_value=mock_session),
        patch("orchid.orchestrator.Orchestrator", return_value=mock_orch),
    ):
        from orchid.multi import worker_main
        worker_main(str(tmp_path), queue, semaphore, local_semaphore, stop_event, code_model=None)

    items = queue.drain()
    assert len(items) > 0
    assert all("event" in item for item in items)
    assert all("project" in item for item in items)
    assert all("data" in item for item in items)

    task_start = next(i for i in items if i["event"] == "task_start")
    assert task_start["data"]["task_id"] == "T001"


def _make_semaphore_tracker(sem: multiprocessing.Semaphore) -> tuple[multiprocessing.Semaphore, list[str]]:
    """Wrap a semaphore so acquire/release calls are recorded."""
    calls: list[str] = []
    real_acquire = sem.acquire
    real_release = sem.release

    def _acq():
        calls.append("acquire")
        return real_acquire()

    def _rel():
        calls.append("release")
        return real_release()

    sem.acquire = _acq  # type: ignore[method-assign]
    sem.release = _rel  # type: ignore[method-assign]
    return sem, calls


def _make_mock_registry(api_key: str = "claude", local_key: str = "local") -> MagicMock:
    """Return a mock registry whose get_by_key() returns the right provider type."""
    from orchid.providers.anthropic import AnthropicProvider
    from orchid.providers.local import LocalProvider
    from orchid.providers.ollama import OllamaProvider

    api_provider = MagicMock(spec=AnthropicProvider)
    local_provider = MagicMock(spec=LocalProvider)
    ollama_provider = MagicMock(spec=OllamaProvider)

    def _get(key: str):
        if key == api_key:
            return api_provider
        if key == "ollama":
            return ollama_provider
        return local_provider

    registry = MagicMock()
    registry.get_by_key.side_effect = _get
    return registry


def test_api_semaphore_used_for_api_providers():
    """_install_semaphore_wrapper routes API providers (claude, openai, bedrock) to api_semaphore."""
    import orchid.tools.models as _models

    original_call = _models.call
    api_sem, api_calls = _make_semaphore_tracker(multiprocessing.Semaphore(2))
    local_sem, local_calls = _make_semaphore_tracker(multiprocessing.Semaphore(1))

    try:
        from orchid.multi import _install_semaphore_wrapper

        with (
            patch.object(_models, "call", return_value="result"),
            patch("orchid.providers.registry.get_registry", return_value=_make_mock_registry()),
        ):
            _install_semaphore_wrapper(api_sem, local_sem)

            _models.call([], model_key="claude")
            assert api_calls == ["acquire", "release"]
            assert local_calls == []

            _models.call([], model_key="local")
            assert api_calls == ["acquire", "release"]   # unchanged
            assert local_calls == ["acquire", "release"]
    finally:
        _models.call = original_call


def test_local_semaphore_used_for_ollama():
    """_install_semaphore_wrapper routes OllamaProvider calls to local_semaphore."""
    import orchid.tools.models as _models

    original_call = _models.call
    api_sem, api_calls = _make_semaphore_tracker(multiprocessing.Semaphore(2))
    local_sem, local_calls = _make_semaphore_tracker(multiprocessing.Semaphore(1))

    try:
        from orchid.multi import _install_semaphore_wrapper

        with (
            patch.object(_models, "call", return_value="result"),
            patch("orchid.providers.registry.get_registry", return_value=_make_mock_registry()),
        ):
            _install_semaphore_wrapper(api_sem, local_sem)

            _models.call([], model_key="ollama")
            assert local_calls == ["acquire", "release"]
            assert api_calls == []
    finally:
        _models.call = original_call


def test_semaphore_fallback_on_registry_error():
    """When registry lookup fails, falls back to model_key == 'local' heuristic."""
    import orchid.tools.models as _models

    original_call = _models.call
    api_sem, api_calls = _make_semaphore_tracker(multiprocessing.Semaphore(2))
    local_sem, local_calls = _make_semaphore_tracker(multiprocessing.Semaphore(1))

    broken_registry = MagicMock()
    broken_registry.get_by_key.side_effect = RuntimeError("registry unavailable")

    try:
        from orchid.multi import _install_semaphore_wrapper

        with (
            patch.object(_models, "call", return_value="result"),
            patch("orchid.providers.registry.get_registry", return_value=broken_registry),
        ):
            _install_semaphore_wrapper(api_sem, local_sem)

            # "local" → local_semaphore via fallback heuristic
            _models.call([], model_key="local")
            assert local_calls == ["acquire", "release"]
            assert api_calls == []

            # "claude" → api_semaphore via fallback heuristic
            _models.call([], model_key="claude")
            assert api_calls == ["acquire", "release"]
    finally:
        _models.call = original_call


# ── MultiOrchid coordinator ────────────────────────────────────────────────────


def test_coordinator_spawns_correct_worker_count(tmp_path):
    """MultiOrchid spawns exactly one process per project."""
    from orchid.multi import MultiOrchid

    proj_a = tmp_path / "project_a"
    proj_b = tmp_path / "project_b"
    proj_a.mkdir()
    proj_b.mkdir()

    spawned: list[str] = []

    def _fake_spawn(self, project_path: str) -> None:
        spawned.append(project_path)
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = False
        mock_proc.exitcode = 0
        mock_proc.pid = 12345
        self._workers[project_path] = mock_proc

    with patch.object(MultiOrchid, "_spawn_worker", _fake_spawn):
        orch = MultiOrchid(projects=[proj_a, proj_b])
        # Manually drive the coordinator loop one cycle
        orch._coordinator_loop = MagicMock()  # skip the blocking loop
        orch.start()

    assert len(spawned) == 2
    assert any(str(proj_a) in s for s in spawned)
    assert any(str(proj_b) in s for s in spawned)


def test_worker_crash_triggers_restart(tmp_path):
    """_check_worker_health restarts a crashed worker."""
    from orchid.multi import MultiOrchid

    proj = tmp_path / "proj"
    proj.mkdir()
    proj_str = str(proj)

    orch = MultiOrchid(projects=[proj])
    orch._restart_on_crash = True
    orch._max_restarts = 3

    # Set up a crashed worker
    crashed_proc = MagicMock()
    crashed_proc.is_alive.return_value = False
    crashed_proc.exitcode = 1
    orch._workers[proj_str] = crashed_proc

    spawned: list[str] = []

    def _fake_spawn(project_path: str) -> None:
        spawned.append(project_path)
        new_proc = MagicMock()
        new_proc.is_alive.return_value = True
        new_proc.exitcode = None
        orch._workers[project_path] = new_proc

    with patch.object(orch, "_spawn_worker", _fake_spawn):
        orch._check_worker_health()

    assert len(spawned) == 1
    assert orch._restart_counts[proj_str] == 1


def test_max_restarts_respected(tmp_path):
    """_check_worker_health does NOT restart after max_restarts is exceeded."""
    from orchid.multi import MultiOrchid

    proj = tmp_path / "proj"
    proj.mkdir()
    proj_str = str(proj)

    orch = MultiOrchid(projects=[proj])
    orch._restart_on_crash = True
    orch._max_restarts = 2
    orch._restart_counts[proj_str] = 2  # already at max
    # Replace the multiprocessing.Queue with our sync version to avoid feeder-thread timing
    sync_q = _SyncQueue()
    orch._notification_queue = sync_q

    crashed_proc = MagicMock()
    crashed_proc.is_alive.return_value = False
    crashed_proc.exitcode = 1
    orch._workers[proj_str] = crashed_proc

    spawned: list[str] = []

    with patch.object(orch, "_spawn_worker", lambda p: spawned.append(p)):
        orch._check_worker_health()

    assert len(spawned) == 0  # no restart

    # Should emit worker_failed notification
    items = sync_q.drain()
    assert any(i["event"] == "worker_failed" for i in items)


def test_multi_status_aggregates_projects(tmp_path):
    """status() returns info for all managed projects."""
    from orchid.multi import MultiOrchid

    proj_a = tmp_path / "alpha"
    proj_b = tmp_path / "beta"
    proj_a.mkdir()
    proj_b.mkdir()

    orch = MultiOrchid(projects=[proj_a, proj_b])

    proc_a = MagicMock()
    proc_a.is_alive.return_value = True
    proc_a.pid = 100
    proc_a.exitcode = None

    proc_b = MagicMock()
    proc_b.is_alive.return_value = False
    proc_b.pid = 200
    proc_b.exitcode = 0

    orch._workers = {str(proj_a): proc_a, str(proj_b): proc_b}

    result = orch.status()
    assert "alpha" in result
    assert "beta" in result
    assert result["alpha"]["alive"] is True
    assert result["beta"]["alive"] is False


# ── multi_formatter ────────────────────────────────────────────────────────────


def test_telegram_tags_messages_with_project_name():
    """tag_message prepends [project] to all messages."""
    from orchid.interfaces.multi_formatter import tag_message

    msg = tag_message("webtron", "Task T001 done")
    assert msg.startswith("[webtron]")
    assert "Task T001 done" in msg


def test_tag_truncates_long_project_name():
    """tag_message truncates project name to 10 chars."""
    from orchid.interfaces.multi_formatter import tag_message

    msg = tag_message("a-very-long-project-name", "hello")
    tag = msg.split("]")[0][1:]
    assert len(tag) <= 10


def test_format_notification_tags_standard_events():
    """format_notification tags task_complete with project name."""
    from orchid.interfaces.multi_formatter import format_notification

    msg = format_notification("task_complete", "webtron", {
        "task_id": "T001",
        "result_snippet": "Built the login page",
    })
    assert msg is not None
    assert "webtron" in msg
    assert "T001" in msg


def test_format_notification_worker_restart():
    """format_notification formats worker_restart correctly."""
    from orchid.interfaces.multi_formatter import format_notification

    msg = format_notification("worker_restart", "myapp", {
        "restart_count": 2,
        "max_restarts": 3,
    })
    assert msg is not None
    assert "myapp" in msg
    assert "2/3" in msg
    assert "❌" in msg


def test_format_notification_worker_failed():
    """format_notification formats worker_failed correctly."""
    from orchid.interfaces.multi_formatter import format_notification

    msg = format_notification("worker_failed", "myapp", {"max_restarts": 3})
    assert msg is not None
    assert "🔴" in msg
    assert "3" in msg


def test_format_notification_unknown_event_returns_none():
    """format_notification returns None for events that have no display."""
    from orchid.interfaces.multi_formatter import format_notification

    msg = format_notification("completely_unknown_event", "proj", {})
    assert msg is None


def test_format_multi_status_all_projects():
    """format_multi_status lists all projects with status icons."""
    from orchid.interfaces.multi_formatter import format_multi_status

    statuses = {
        "webtron": {"alive": True, "pid": 100, "restarts": 0},
        "cozy-chair": {"alive": False, "pid": 200, "restarts": 1},
    }
    msg = format_multi_status(statuses)
    assert "webtron" in msg
    assert "cozy-chair" in msg
    assert "🟢" in msg  # alive
    assert "⭕" in msg  # not alive


# ── CLI argument parsing ───────────────────────────────────────────────────────


def test_cli_multi_flag_parses_multiple_projects(tmp_path):
    """CLI --multi with multiple --project flags calls _cmd_multi."""
    from typer.testing import CliRunner

    from orchid.interfaces.cli import app

    proj_a = tmp_path / "alpha"
    proj_b = tmp_path / "beta"
    proj_a.mkdir()
    proj_b.mkdir()

    runner = CliRunner()

    with patch("orchid.interfaces.cli._cmd_multi") as mock_multi:
        result = runner.invoke(app, [
            "--multi",
            "--project", str(proj_a),
            "--project", str(proj_b),
        ])
        assert mock_multi.called
        called_projects = mock_multi.call_args[0][0]
        assert any(str(proj_a) in p for p in called_projects)
        assert any(str(proj_b) in p for p in called_projects)


def test_cli_multi_requires_two_projects(tmp_path):
    """--multi with only one --project prints an error."""
    from typer.testing import CliRunner

    from orchid.interfaces.cli import app

    proj_a = tmp_path / "alpha"
    proj_a.mkdir()

    runner = CliRunner()
    result = runner.invoke(app, ["--multi", "--project", str(proj_a)])
    assert result.exit_code != 0 or "requires at least two" in result.output
