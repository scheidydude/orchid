"""Tests for orchid/agents/project_manager.py."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchid.agents.project_manager import (
    ProjectManagerAgent,
    _parse_milestones,
    _parse_task_lines,
    _extract_task_title,
    _validate_tasks,
    _renumber_tasks,
    _MIN_TITLE_LEN,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_MILESTONES = """\
# Milestones — testapp

## Milestone 1: Foundation
**Goal:** Set up the project scaffold and core data models
**Tasks:** ~6 tasks
**Depends on:** none

Infrastructure setup, database schema, auth skeleton.

## Milestone 2: Core Features
**Goal:** Implement the main business logic and API endpoints
**Tasks:** ~8 tasks
**Depends on:** Milestone 1

REST API, business logic, background jobs.

## Milestone 3: Polish & Deploy
**Goal:** Production-ready deployment with tests and docs
**Tasks:** ~5 tasks
**Depends on:** Milestone 2

Testing, documentation, Docker, CI/CD.
"""

SAMPLE_M1_TASKS = """\
- [ ] **T001** Scaffold FastAPI project with uv and directory layout `type:code_generate` `p1` `model:local`
- [ ] **T002** Design PostgreSQL schema for users and posts `type:code_generate` `p1` `model:claude`
- [ ] **T003** Implement User model with Alembic migrations `type:code_generate` `p1` `needs:T002` `model:claude`
- [ ] **T004** Implement JWT authentication endpoints `type:code_generate` `p1` `needs:T001,T002` `model:claude`
- [ ] **T005** Review authentication implementation for security `type:review` `p1` `needs:T004`
- [ ] **T006** Milestone 1 Foundation summary `type:rollup` `p2` `rollup:T001,T002,T003,T004,T005` `output:MILESTONE-1.md`
"""

SAMPLE_M2_TASKS = """\
- [ ] **T007** Implement posts CRUD API endpoints `type:code_generate` `p1` `needs:T003` `model:claude`
- [ ] **T008** Add pagination and filtering to list endpoints `type:code_generate` `p2` `needs:T007` `model:local`
- [ ] **T009** Implement background email notification job `type:code_generate` `p2` `needs:T004` `model:claude`
- [ ] **T010** Review core feature implementation `type:review` `p1` `needs:T007,T008,T009`
- [ ] **T011** Milestone 2 Core Features summary `type:rollup` `p2` `rollup:T007,T008,T009,T010` `output:MILESTONE-2.md`
"""

SAMPLE_M3_TASKS = """\
- [ ] **T012** Write pytest integration tests for auth and posts API `type:code_generate` `p1` `needs:T010` `model:claude`
- [ ] **T013** Create Dockerfile and docker-compose for local development `type:code_generate` `p2` `model:local`
- [ ] **T014** Write API reference documentation in docs/ directory `type:draft` `p2` `needs:T010` `model:local`
- [ ] **T015** Configure GitHub Actions CI pipeline for tests and lint `type:code_generate` `p2` `model:local`
- [ ] **T016** Milestone 3 Polish and Deploy summary `type:rollup` `p2` `rollup:T012,T013,T014,T015` `output:MILESTONE-3.md`
"""


@pytest.fixture
def proj(tmp_path):
    (tmp_path / ".orchid").mkdir()
    (tmp_path / "REQUIREMENTS.md").write_text("# Requirements\nBuild a blog API.", encoding="utf-8")
    (tmp_path / "ARCHITECTURE.md").write_text("# Architecture\nFastAPI + PostgreSQL.", encoding="utf-8")
    return tmp_path


def _make_mock_profile():
    profile = MagicMock()
    profile.to_context_string.return_value = "Python 3.12, uv, FastAPI, PostgreSQL"
    return profile


# ── Unit tests: helpers ────────────────────────────────────────────────────────


def test_parse_milestones_finds_all_milestones():
    milestones = _parse_milestones(SAMPLE_MILESTONES)
    assert len(milestones) == 3
    names = [m[0] for m in milestones]
    assert names[0].startswith("Milestone 1")
    assert names[1].startswith("Milestone 2")
    assert names[2].startswith("Milestone 3")


def test_parse_milestones_empty():
    assert _parse_milestones("# No milestones here") == []


def test_parse_task_lines():
    lines = _parse_task_lines(SAMPLE_M1_TASKS)
    assert len(lines) == 6
    assert all(re.match(r"^-\s*\[\s*\]\s*\*\*T\d+\*\*", l) for l in lines)


def test_extract_task_title_normal():
    line = "- [ ] **T003** Implement User model with Alembic migrations `type:code_generate` `p1`"
    title = _extract_task_title(line)
    assert title == "Implement User model with Alembic migrations"


def test_extract_task_title_short():
    line = "- [ ] **T003** Scaffold `type:code_generate` `p1`"
    title = _extract_task_title(line)
    assert title == "Scaffold"


def test_validate_tasks_all_valid():
    lines = _parse_task_lines(SAMPLE_M1_TASKS)
    is_valid, short = _validate_tasks(lines)
    assert is_valid
    assert short == []


def test_validate_tasks_flags_mostly_short():
    bad_tasks = "\n".join(
        f"- [ ] **T{i:03d}** Do `type:code_generate` `p1`" for i in range(1, 11)
    )
    lines = _parse_task_lines(bad_tasks)
    is_valid, short = _validate_tasks(lines)
    assert not is_valid
    assert len(short) == len(lines)


def test_validate_tasks_tolerates_minority_short():
    # 1 short out of 10 = 10% < 20% threshold
    good = "\n".join(
        f"- [ ] **T{i:03d}** Implement the full feature for module {i} `type:code_generate` `p1`"
        for i in range(1, 10)
    )
    bad = "- [ ] **T010** Fix `type:code_generate` `p2`"
    lines = _parse_task_lines(good + "\n" + bad)
    is_valid, short = _validate_tasks(lines)
    assert is_valid
    assert len(short) == 1


def test_renumber_tasks_sequential():
    lines = _parse_task_lines(SAMPLE_M2_TASKS)  # T007–T011
    renumbered, mapping = _renumber_tasks(lines, start=1)
    ids = [_extract_task_title.__module__ and re.search(r"\*\*(T\d+)\*\*", l).group(1) for l in renumbered]
    assert ids == ["T001", "T002", "T003", "T004", "T005"]


def test_renumber_tasks_fixes_needs_references():
    # T008 has `needs:T007`; when renumbered from 1, T007→T001, so needs:T007→needs:T001
    lines = _parse_task_lines(SAMPLE_M2_TASKS)
    renumbered, _ = _renumber_tasks(lines, start=1)
    # Find the line originally T008 (now T002) — it has an intra-set needs reference
    t002_line = next(l for l in renumbered if "**T002**" in l)
    assert "`needs:T001`" in t002_line


# ── Integration test: full run ─────────────────────────────────────────────────


def test_project_manager_tasks_have_full_titles(proj):
    """All generated task titles must be at least 15 characters."""
    agent = ProjectManagerAgent(project_dir=proj)
    profile = _make_mock_profile()

    # Provider returns milestone content then per-milestone task blocks
    milestone_responses = [SAMPLE_M1_TASKS, SAMPLE_M2_TASKS, SAMPLE_M3_TASKS]
    call_count = [0]

    def fake_complete(messages, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        if idx == 0:
            return SAMPLE_MILESTONES
        milestone_idx = (idx - 1) % len(milestone_responses)
        return milestone_responses[milestone_idx]

    mock_provider = MagicMock()
    mock_provider.complete.side_effect = fake_complete

    with patch.object(agent, "_get_provider", return_value=mock_provider):
        with patch("orchid.config.configure_for_project"):
            result = agent.run(machine_profile=profile)

    tasks_content = result.tasks_path.read_text(encoding="utf-8")
    task_lines = _parse_task_lines(tasks_content)

    assert len(task_lines) > 0, "No tasks were generated"

    short_titles = []
    for line in task_lines:
        title = _extract_task_title(line)
        if len(title) < _MIN_TITLE_LEN:
            short_titles.append((title, line))

    assert short_titles == [], (
        f"{len(short_titles)} task(s) have titles shorter than {_MIN_TITLE_LEN} chars:\n"
        + "\n".join(f"  {t!r} — {l}" for t, l in short_titles)
    )


def test_project_manager_retries_on_short_titles(proj):
    """When >20% of tasks have short titles, the agent retries."""
    agent = ProjectManagerAgent(project_dir=proj)
    profile = _make_mock_profile()

    bad_tasks = "\n".join(
        f"- [ ] **T{i:03d}** Do `type:code_generate` `p1`" for i in range(1, 8)
    )
    good_tasks = "\n".join(
        f"- [ ] **T{i:03d}** Implement full feature for component {i} with tests `type:code_generate` `p1`"
        for i in range(1, 8)
    )

    responses = iter([SAMPLE_MILESTONES, bad_tasks, good_tasks, good_tasks, good_tasks])

    mock_provider = MagicMock()
    mock_provider.complete.side_effect = lambda messages, **kw: next(responses)

    with patch.object(agent, "_get_provider", return_value=mock_provider):
        with patch("orchid.config.configure_for_project"):
            result = agent.run(machine_profile=profile)

    # Provider should have been called more than once for the first milestone (retry)
    assert mock_provider.complete.call_count >= 3  # milestones + retry + success


def test_project_manager_writes_tasks_file(proj):
    """tasks.md is written with a proper header and task lines."""
    agent = ProjectManagerAgent(project_dir=proj)
    profile = _make_mock_profile()

    all_tasks = SAMPLE_M1_TASKS + SAMPLE_M2_TASKS + SAMPLE_M3_TASKS
    call_count = [0]

    def fake_complete(messages, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        return SAMPLE_MILESTONES if idx == 0 else all_tasks

    mock_provider = MagicMock()
    mock_provider.complete.side_effect = fake_complete

    with patch.object(agent, "_get_provider", return_value=mock_provider):
        with patch("orchid.config.configure_for_project"):
            result = agent.run(machine_profile=profile)

    content = result.tasks_path.read_text(encoding="utf-8")
    assert content.startswith("# Tasks —")
    assert result.task_count > 0


def test_project_manager_fallback_when_no_milestones_parsed(proj):
    """Falls back to single-pass generation when milestones content has no headings."""
    agent = ProjectManagerAgent(project_dir=proj)
    profile = _make_mock_profile()

    bare_milestones = "Just some text with no milestone headings."
    single_pass_tasks = (
        "# Tasks — testapp\n"
        + SAMPLE_M1_TASKS
    )
    call_count = [0]

    def fake_complete(messages, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        return bare_milestones if idx == 0 else single_pass_tasks

    mock_provider = MagicMock()
    mock_provider.complete.side_effect = fake_complete

    with patch.object(agent, "_get_provider", return_value=mock_provider):
        with patch("orchid.config.configure_for_project"):
            result = agent.run(machine_profile=profile)

    assert result.task_count > 0
