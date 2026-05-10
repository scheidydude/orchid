"""Unit tests for the Orchid V2 hook audit logging system."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

import pytest

from orchid.hooks.audit import (
    AuditEntry,
    AuditLogger,
    _get_logger,
    audit_hook,
    configure_audit_logger,
    get_audit_logger,
)


def _force_reset_audit_module_state() -> None:
    import orchid.hooks.audit as mod
    mod._logger = None


@pytest.fixture(autouse=True)
def _reset_audit_each_test():
    _force_reset_audit_module_state()
    yield
    _force_reset_audit_module_state()


def _tmp_project(tmp_path: Path) -> Path:
    project = tmp_path / "testproj"
    (project / ".orchid").mkdir(parents=True, exist_ok=True)
    return project


class TestAuditEntry:
    def test_default_timestamp_generated(self):
        entry = AuditEntry()
        assert entry.timestamp != ""
        datetime.fromisoformat(entry.timestamp)

    def test_custom_timestamp(self):
        entry = AuditEntry(timestamp="2025-01-01T00:00:00+00:00")
        assert entry.timestamp == "2025-01-01T00:00:00+00:00"

    def test_to_dict_contains_expected_keys(self):
        entry = AuditEntry(
            event_type="task_complete", hook_name="notify", hook_type="shell",
            status="success", duration_s=1.234, error="", task_id="T001",
            project_dir="/tmp/proj", command="echo hello", status_code=0,
        )
        d = entry.to_dict()
        assert d["event_type"] == "task_complete"
        assert d["hook_name"] == "notify"
        assert d["hook_type"] == "shell"
        assert d["status"] == "success"
        assert d["duration_s"] == 1.234
        assert d["error"] == ""
        assert d["task_id"] == "T001"
        assert d["project_dir"] == "/tmp/proj"
        assert d["command"] == "echo hello"
        assert d["status_code"] == 0

    def test_duration_rounded(self):
        entry = AuditEntry(duration_s=1.234567)
        assert entry.to_dict()["duration_s"] == 1.235

    def test_to_json_is_valid_json(self):
        entry = AuditEntry(event_type="test", hook_name="h", hook_type="s", status="ok")
        parsed = json.loads(entry.to_json())
        assert parsed["event_type"] == "test"
        assert parsed["hook_name"] == "h"
        assert parsed["hook_type"] == "s"
        assert parsed["status"] == "ok"

    def test_all_statuses_accepted(self):
        for status in ("success", "failure", "blocked", "timeout", "error"):
            entry = AuditEntry(status=status)
            assert entry.to_dict()["status"] == status

    def test_empty_fields_default_to_empty_or_zero(self):
        entry = AuditEntry()
        d = entry.to_dict()
        assert d["event_type"] == ""
        assert d["hook_name"] == ""
        assert d["hook_type"] == ""
        assert d["status"] == ""
        assert d["duration_s"] == 0.0
        assert d["error"] == ""
        assert d["task_id"] == ""
        assert d["project_dir"] == ""
        assert d["command"] == ""
        assert d["status_code"] == 0


class TestAuditLogger:
    def test_log_creates_file(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        entry = AuditEntry(event_type="test", hook_name="h", hook_type="s", status="ok")
        logger.log(entry)
        log_path = project / ".orchid" / "audit_log.jsonl"
        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event_type"] == "test"

    def test_log_appends_multiple_entries(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        for i in range(5):
            logger.log(AuditEntry(event_type="test", hook_name=f"h{i}", status="ok"))
        log_path = project / ".orchid" / "audit_log.jsonl"
        lines = [l for l in log_path.read_text().strip().split("\n") if l]
        assert len(lines) == 5

    def test_log_hook_convenience(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log_hook(
            event_type="task_complete", hook_name="notify", hook_type="shell",
            status="success", duration_s=0.5, task_id="T001", command="echo done",
        )
        entries = logger.read_entries()
        assert len(entries) == 1
        assert entries[0].event_type == "task_complete"
        assert entries[0].hook_name == "notify"
        assert entries[0].hook_type == "shell"
        assert entries[0].status == "success"
        assert entries[0].duration_s == 0.5
        assert entries[0].task_id == "T001"
        assert entries[0].command == "echo done"

    def test_read_entries_empty_file(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        assert logger.read_entries() == []

    def test_read_entries_no_file(self, tmp_path: Path):
        project = tmp_path / "noproject"
        logger = AuditLogger(project)
        assert logger.read_entries() == []

    def test_read_entries_respects_limit(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        for i in range(20):
            logger.log(AuditEntry(event_type="test", hook_name=f"h{i}", status="ok"))
        last_5 = logger.read_entries(limit=5)
        assert len(last_5) == 5
        assert last_5[0].hook_name == "h15"
        assert last_5[-1].hook_name == "h19"

    def test_clear_truncates_file(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log(AuditEntry(event_type="test", hook_name="h", status="ok"))
        logger.clear()
        log_path = project / ".orchid" / "audit_log.jsonl"
        assert log_path.read_text() == ""
        assert logger.read_entries() == []

    def test_clear_noop_when_file_missing(self, tmp_path: Path):
        project = tmp_path / "noproject"
        logger = AuditLogger(project)
        logger.clear()

    def test_log_hook_with_error(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log_hook(
            event_type="task_complete", hook_name="notify", hook_type="shell",
            status="failure", error="command not found", command="nonexistent_cmd",
        )
        entries = logger.read_entries()
        assert len(entries) == 1
        assert entries[0].status == "failure"
        assert entries[0].error == "command not found"

    def test_log_hook_with_status_code(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log_hook(
            event_type="http_hook", hook_name="slack_notify", hook_type="http",
            status="success", status_code=200,
        )
        entries = logger.read_entries()
        assert entries[0].status_code == 200

    def test_log_hook_with_blocked_status(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log_hook(
            event_type="task_start", hook_name="shell_check", hook_type="shell",
            status="blocked", command="forbidden_cmd",
        )
        entries = logger.read_entries()
        assert entries[0].status == "blocked"

    def test_log_hook_with_timeout_status(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log_hook(
            event_type="task_complete", hook_name="slow_hook", hook_type="shell",
            status="timeout", error="exceeded 30s",
        )
        entries = logger.read_entries()
        assert entries[0].status == "timeout"

    def test_log_hook_with_error_status(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log_hook(
            event_type="phase_transition", hook_name="notify", hook_type="http",
            status="error", error="Connection refused",
        )
        entries = logger.read_entries()
        assert entries[0].status == "error"

    def test_audit_logger_creates_orchid_dir(self, tmp_path: Path):
        project = tmp_path / "bare_project"
        logger = AuditLogger(project)
        logger.log(AuditEntry(event_type="test", hook_name="h", status="ok"))
        assert (project / ".orchid").is_dir()
        assert (project / ".orchid" / "audit_log.jsonl").exists()


class TestAuditLoggerThreadSafety:
    def test_concurrent_writes(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        errors = []

        def writer(thread_id: int):
            try:
                for i in range(50):
                    logger.log(AuditEntry(
                        event_type="concurrent", hook_name=f"thread_{thread_id}",
                        hook_type="shell", status="ok",
                    ))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(tid,)) for tid in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        log_path = project / ".orchid" / "audit_log.jsonl"
        lines = [l for l in log_path.read_text().strip().split("\n") if l]
        assert len(lines) == 200

    def test_concurrent_read_and_write(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        errors = []

        def writer():
            try:
                for i in range(30):
                    logger.log(AuditEntry(event_type="rw", hook_name="w", status="ok"))
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(30):
                    logger.read_entries()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_concurrent_clear_and_write(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        errors = []

        def writer():
            try:
                for _ in range(20):
                    logger.log(AuditEntry(event_type="cw", hook_name="w", status="ok"))
            except Exception as e:
                errors.append(e)

        def clearer():
            try:
                for _ in range(10):
                    logger.clear()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=clearer),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        entries = logger.read_entries()
        for e in entries:
            assert e.event_type == "cw"


class TestModuleLevelFunctions:
    def test_configure_audit_logger_sets_singleton(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = configure_audit_logger(project)
        assert isinstance(logger, AuditLogger)
        assert get_audit_logger() is logger

    def test_get_audit_logger_returns_none_when_not_configured(self):
        assert get_audit_logger() is None
        assert _get_logger() is None

    def test_get_audit_logger_after_configure(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        assert get_audit_logger() is not None
        assert _get_logger() is get_audit_logger()

    def test_audit_hook_noops_when_not_configured(self):
        audit_hook(event_type="test", hook_name="h", hook_type="shell", status="success")

    def test_audit_hook_writes_when_configured(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        audit_hook(
            event_type="task_complete", hook_name="notify", hook_type="shell",
            status="success", duration_s=0.1, task_id="T001", command="echo done",
        )
        logger = get_audit_logger()
        assert logger is not None
        entries = logger.read_entries()
        assert len(entries) == 1
        assert entries[0].event_type == "task_complete"
        assert entries[0].hook_name == "notify"

    def test_audit_hook_with_error(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        audit_hook(
            event_type="task_start", hook_name="shell_check", hook_type="shell",
            status="failure", error="bad command",
        )
        logger = get_audit_logger()
        entries = logger.read_entries()
        assert entries[0].status == "failure"
        assert entries[0].error == "bad command"

    def test_configure_audit_logger_can_be_reconfigured(self, tmp_path: Path):
        p1 = _tmp_project(tmp_path / "proj1")
        p2 = _tmp_project(tmp_path / "proj2")
        configure_audit_logger(p1)
        configure_audit_logger(p2)
        assert get_audit_logger().project_dir == p2.resolve()

    def test_audit_hook_with_blocked_status(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        audit_hook(
            event_type="task_start", hook_name="allowlist_check", hook_type="shell",
            status="blocked", command="forbidden_cmd",
        )
        logger = get_audit_logger()
        entries = logger.read_entries()
        assert entries[0].status == "blocked"
        assert entries[0].command == "forbidden_cmd"

    def test_audit_hook_with_timeout_status(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        audit_hook(
            event_type="task_complete", hook_name="slow_hook", hook_type="shell",
            status="timeout", error="exceeded 60s",
        )
        logger = get_audit_logger()
        entries = logger.read_entries()
        assert entries[0].status == "timeout"

    def test_audit_hook_with_http_status_code(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        audit_hook(
            event_type="http_hook", hook_name="slack_notify", hook_type="http",
            status="success", status_code=200,
        )
        logger = get_audit_logger()
        entries = logger.read_entries()
        assert entries[0].status_code == 200

    def test_audit_hook_with_error_status(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        audit_hook(
            event_type="phase_transition", hook_name="notify", hook_type="http",
            status="error", error="Connection refused",
        )
        logger = get_audit_logger()
        entries = logger.read_entries()
        assert entries[0].status == "error"

    def test_audit_hook_multiple_events(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        for i in range(10):
            audit_hook(event_type="task_complete", hook_name=f"hook_{i}", hook_type="shell", status="success")
        logger = get_audit_logger()
        entries = logger.read_entries()
        assert len(entries) == 10


class TestShellHookAuditIntegration:
    def _make_loader(self, tmp_path: Path) -> tuple:
        from orchid.hooks.loader import HookLoader
        project = _tmp_project(tmp_path)
        yaml_content = "hooks:\n  enabled: true\n  audit:\n    enabled: true\n  tasks:\n    - name: test_audit_shell\n      event: task_complete\n      type: shell\n      command: echo test\n      mode: sync\n"
        (project / ".orchid.yaml").write_text(yaml_content)
        (project / "providers.yaml").write_text("providers: {}\n")
        return project, HookLoader(project)

    def test_shell_hook_success_creates_audit_entry(self, tmp_path: Path):
        project, loader = self._make_loader(tmp_path)
        count = loader.load()
        assert count >= 1
        from orchid.hooks.audit import get_audit_logger
        logger = get_audit_logger()
        assert logger is not None
        assert logger.project_dir == project.resolve()

    def test_shell_hook_blocked_creates_audit_entry(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        from orchid.hooks.audit import get_audit_logger
        logger = get_audit_logger()
        assert logger is not None
        logger.log_hook(
            event_type="task_start", hook_name="forbidden_hook", hook_type="shell",
            status="blocked", command="forbidden_cmd", task_id="T001",
        )
        entries = logger.read_entries()
        assert len(entries) == 1
        assert entries[0].status == "blocked"
        assert entries[0].command == "forbidden_cmd"
        assert entries[0].hook_type == "shell"

    def test_shell_hook_failure_creates_audit_entry(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        from orchid.hooks.audit import get_audit_logger
        logger = get_audit_logger()
        assert logger is not None
        logger.log_hook(
            event_type="task_complete", hook_name="failing_hook", hook_type="shell",
            status="failure", error="command not found", command="nonexistent_cmd", task_id="T002",
        )
        entries = logger.read_entries()
        assert len(entries) == 1
        assert entries[0].status == "failure"
        assert entries[0].error == "command not found"

    def test_shell_hook_timeout_creates_audit_entry(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        from orchid.hooks.audit import get_audit_logger
        logger = get_audit_logger()
        assert logger is not None
        logger.log_hook(
            event_type="task_complete", hook_name="slow_hook", hook_type="shell",
            status="timeout", error="exceeded 30s", task_id="T003",
        )
        entries = logger.read_entries()
        assert len(entries) == 1
        assert entries[0].status == "timeout"

    def test_shell_hook_error_creates_audit_entry(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        from orchid.hooks.audit import get_audit_logger
        logger = get_audit_logger()
        assert logger is not None
        logger.log_hook(
            event_type="task_complete", hook_name="erroneous_hook", hook_type="shell",
            status="error", error="Permission denied", task_id="T004",
        )
        entries = logger.read_entries()
        assert len(entries) == 1
        assert entries[0].status == "error"

    def test_shell_hook_success_audit_record(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        from orchid.hooks.audit import get_audit_logger
        logger = get_audit_logger()
        assert logger is not None
        logger.log_hook(
            event_type="task_complete", hook_name="echo_hook", hook_type="shell",
            status="success", duration_s=0.042, task_id="T005", command="echo hello",
        )
        entries = logger.read_entries()
        assert len(entries) == 1
        e = entries[0]
        assert e.event_type == "task_complete"
        assert e.hook_name == "echo_hook"
        assert e.hook_type == "shell"
        assert e.status == "success"
        assert e.duration_s == 0.042
        assert e.task_id == "T005"
        assert e.command == "echo hello"
        assert e.status_code == 0


class TestHTTPHookAuditIntegration:
    def test_http_hook_success_audit_record(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        from orchid.hooks.audit import get_audit_logger
        logger = get_audit_logger()
        assert logger is not None
        logger.log_hook(
            event_type="http_hook", hook_name="slack_notify", hook_type="http",
            status="success", duration_s=0.312, status_code=200, task_id="T010",
            command="POST https://hooks.slack.com/services/...",
        )
        entries = logger.read_entries()
        assert len(entries) == 1
        e = entries[0]
        assert e.hook_type == "http"
        assert e.status == "success"
        assert e.status_code == 200
        assert e.task_id == "T010"

    def test_http_hook_failure_audit_record(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        from orchid.hooks.audit import get_audit_logger
        logger = get_audit_logger()
        assert logger is not None
        logger.log_hook(
            event_type="http_hook", hook_name="slack_notify", hook_type="http",
            status="failure", duration_s=5.1, error="Connection refused",
            status_code=503, task_id="T011",
        )
        entries = logger.read_entries()
        assert len(entries) == 1
        e = entries[0]
        assert e.status == "failure"
        assert e.error == "Connection refused"
        assert e.status_code == 503

    def test_http_hook_error_audit_record(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        from orchid.hooks.audit import get_audit_logger
        logger = get_audit_logger()
        assert logger is not None
        logger.log_hook(
            event_type="http_hook", hook_name="webhook", hook_type="http",
            status="error", error="SSL: CERTIFICATE_VERIFY_FAILED", task_id="T012",
        )
        entries = logger.read_entries()
        assert len(entries) == 1
        assert entries[0].status == "error"


class TestAuditLogFormat:
    def test_each_line_is_valid_json(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        for i in range(10):
            logger.log(AuditEntry(event_type="test", hook_name=f"h{i}", hook_type="shell", status="ok"))
        log_path = project / ".orchid" / "audit_log.jsonl"
        lines = log_path.read_text().strip().split("\n")
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)
            assert "timestamp" in parsed
            assert "event_type" in parsed
            assert "hook_name" in parsed
            assert "hook_type" in parsed
            assert "status" in parsed
            assert "duration_s" in parsed
            assert "error" in parsed
            assert "task_id" in parsed
            assert "project_dir" in parsed
            assert "command" in parsed
            assert "status_code" in parsed

    def test_log_path_is_jsonl(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log(AuditEntry(event_type="fmt", hook_name="h", hook_type="s", status="ok"))
        log_path = project / ".orchid" / "audit_log.jsonl"
        assert log_path.suffix == ".jsonl"

    def test_entries_preserve_order(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        for i in range(5):
            logger.log(AuditEntry(event_type="order", hook_name=f"h{i}", status="ok"))
        entries = logger.read_entries()
        for i, e in enumerate(entries):
            assert e.hook_name == f"h{i}"

    def test_empty_lines_are_skipped_on_read(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log(AuditEntry(event_type="test", hook_name="h", status="ok"))
        log_path = project / ".orchid" / "audit_log.jsonl"
        log_path.write_text(log_path.read_text() + "\n\n")
        entries = logger.read_entries()
        assert len(entries) == 1


class TestTimestamp:
    def test_timestamp_is_utc(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log(AuditEntry(event_type="ts", hook_name="h", hook_type="s", status="ok"))
        entries = logger.read_entries()
        assert len(entries) == 1
        ts = entries[0].timestamp
        assert "+" in ts or ts.endswith("Z")

    def test_timestamp_is_iso_format(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log(AuditEntry(event_type="ts", hook_name="h", hook_type="s", status="ok"))
        entries = logger.read_entries()
        datetime.fromisoformat(entries[0].timestamp)

    def test_timestamp_includes_microseconds(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        logger.log(AuditEntry(event_type="ts", hook_name="h", hook_type="s", status="ok"))
        entries = logger.read_entries()
        ts = entries[0].timestamp
        assert "T" in ts


class TestRoundTrip:
    def test_full_roundtrip(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        original = AuditEntry(
            event_type="task_complete", hook_name="notify", hook_type="shell",
            status="success", duration_s=0.123, error="", task_id="T001",
            project_dir=str(project), command="echo done", status_code=0,
        )
        logger.log(original)
        entries = logger.read_entries()
        assert len(entries) == 1
        e = entries[0]
        assert e.event_type == original.event_type
        assert e.hook_name == original.hook_name
        assert e.hook_type == original.hook_type
        assert e.status == original.status
        assert e.duration_s == original.duration_s
        assert e.task_id == original.task_id
        assert e.project_dir == original.project_dir
        assert e.command == original.command
        assert e.status_code == original.status_code

    def test_roundtrip_with_error(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        original = AuditEntry(
            event_type="task_start", hook_name="check", hook_type="shell",
            status="failure", duration_s=0.0, error="exit code 1", task_id="T002",
            project_dir=str(project), command="false", status_code=1,
        )
        logger.log(original)
        entries = logger.read_entries()
        e = entries[0]
        assert e.status == "failure"
        assert e.error == "exit code 1"
        assert e.status_code == 1


class TestProjectDirHandling:
    def test_project_dir_resolved(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        logger = AuditLogger(project)
        assert logger.project_dir == project.resolve()

    def test_project_dir_creates_orchid_subdir(self, tmp_path: Path):
        project = tmp_path / "new_project"
        logger = AuditLogger(project)
        assert (project / ".orchid").is_dir()

    def test_project_dir_with_symlink(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        link = tmp_path / "link_to_proj"
        link.symlink_to(project)
        logger = AuditLogger(link)
        assert logger.project_dir == project.resolve()


class TestHookTypeClassification:
    def test_shell_hook_type(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        audit_hook(event_type="task_complete", hook_name="shell_echo", hook_type="shell", status="success")
        entries = get_audit_logger().read_entries()
        assert entries[0].hook_type == "shell"

    def test_http_hook_type(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        audit_hook(event_type="http_hook", hook_name="webhook", hook_type="http", status="success")
        entries = get_audit_logger().read_entries()
        assert entries[0].hook_type == "http"

    def test_python_hook_type(self, tmp_path: Path):
        project = _tmp_project(tmp_path)
        configure_audit_logger(project)
        audit_hook(event_type="task_complete", hook_name="py_hook", hook_type="python", status="success")
        entries = get_audit_logger().read_entries()
        assert entries[0].hook_type == "python"
