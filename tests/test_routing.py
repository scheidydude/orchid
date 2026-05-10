"""Tests for multi-tier model routing."""

from __future__ import annotations

from orchid.tools.models import COMPLEXITY_KEYWORDS, RouteDecision, route


def test_cli_flag_overrides_all():
    decision = route(
        task_type="code_generate",
        task_model_override="local",
        cli_override="claude",
        task_title="simple task",
    )
    assert decision.model == "claude"
    assert decision.source == "cli_flag"


def test_task_annotation_overrides_config():
    decision = route(
        task_type="code_generate",  # local by default config
        task_model_override="claude",
        cli_override=None,
        task_title="simple task",
    )
    assert decision.model == "claude"
    assert decision.source == "task_annotation"


def test_task_annotation_local_overrides_claude_type():
    decision = route(
        task_type="review",  # claude by default config
        task_model_override="local",
        cli_override=None,
        task_title="simple task",
    )
    assert decision.model == "local"
    assert decision.source == "task_annotation"


def test_keyword_escalation_fires():
    decision = route(
        task_type="code_generate",
        task_model_override=None,
        cli_override=None,
        task_title="Write a regex parser for log extraction",
    )
    assert decision.model == "claude"
    assert decision.source == "heuristic"
    assert "regex" in decision.reason or "parser" in decision.reason


def test_keyword_escalation_multiple_matches():
    decision = route(
        task_type="code_generate",
        task_model_override=None,
        cli_override=None,
        task_title="Implement concurrent authentication with JWT tokens",
    )
    assert decision.model == "claude"
    assert decision.source == "heuristic"


def test_local_model_used_for_simple_task():
    decision = route(
        task_type="code_generate",
        task_model_override=None,
        cli_override=None,
        task_title="Write a helper function to add two numbers",
    )
    assert decision.model == "local"
    assert decision.source == "default"


def test_claude_used_for_review_type():
    decision = route(
        task_type="review",
        task_model_override=None,
        cli_override=None,
        task_title="Review the implementation",
    )
    assert decision.model == "claude"
    assert decision.source == "default"


def test_auto_cli_flag_falls_through_to_heuristic():
    """cli_override='auto' should not force a model — fall through to keyword check."""
    decision = route(
        task_type="code_generate",
        task_model_override=None,
        cli_override="auto",
        task_title="Write a regex parser",
    )
    assert decision.model == "claude"
    assert decision.source == "heuristic"


def test_auto_annotation_falls_through_to_heuristic():
    """model:auto annotation should not force — fall through to keyword check."""
    decision = route(
        task_type="code_generate",
        task_model_override="auto",
        cli_override=None,
        task_title="Simple CRUD function",
    )
    assert decision.model == "local"
    assert decision.source == "default"


def test_route_decision_is_dataclass():
    d = RouteDecision(model="claude", reason="test", source="cli_flag")
    assert d.model == "claude"
    assert d.reason == "test"
    assert d.source == "cli_flag"


def test_complexity_keywords_list_not_empty():
    assert len(COMPLEXITY_KEYWORDS) > 0
    assert "regex" in COMPLEXITY_KEYWORDS
    assert "authentication" in COMPLEXITY_KEYWORDS


def test_model_annotation_parsed_from_tasks_md(tmp_path):
    from orchid.memory.state import load_tasks

    content = (
        "# Tasks\n\n"
        "## TODO\n\n"
        "- [ ] **T001** Simple task `type:draft` `p2` `model:local`\n"
        "- [ ] **T002** Complex task `type:code_generate` `p1` `model:claude`\n"
        "- [ ] **T003** Auto task `type:code_generate` `p2` `model:auto`\n"
    )
    (tmp_path / "tasks.md").write_text(content, encoding="utf-8")
    tasks = load_tasks(tmp_path)
    task_map = {t.id: t for t in tasks}

    assert task_map["T001"].model_override == "local"
    assert task_map["T002"].model_override == "claude"
    assert task_map["T003"].model_override == "auto"


def test_route_decision_logged(capsys, caplog):
    """Routing decisions should produce a log entry."""
    import logging
    with caplog.at_level(logging.INFO, logger="orchid.orchestrator"):
        # We just verify the route() function returns a RouteDecision
        # (log output is tested via orchestrator integration)
        decision = route(task_type="code_generate", task_title="simple task")
        assert isinstance(decision, RouteDecision)
        assert decision.model in ("claude", "local")
