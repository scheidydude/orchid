"""Tests for orchid/project_creator.py — ProjectCreator."""

from unittest.mock import MagicMock, patch

import pytest

from orchid.machine_profile import _DEFAULT_DATA, MachineProfile
from orchid.project_creator import ProjectCreator

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def profile(tmp_path):
    """MachineProfile with tmp_path as default root."""
    import yaml
    data = dict(_DEFAULT_DATA)
    data["project_roots"] = {
        "default": str(tmp_path / "Development"),
        "ai_projects": str(tmp_path / "AI"),
        "type_routing": {
            "ai": str(tmp_path / "AI"),
            "web": str(tmp_path / "Development"),
        },
    }
    data["defaults"]["git_init"] = False  # don't run real git in unit tests
    profile_path = tmp_path / "machine-profile.yaml"
    profile_path.write_text(yaml.dump(data), encoding="utf-8")
    return MachineProfile.load(path=profile_path)


# ── confirm_path ──────────────────────────────────────────────────────────────


def test_confirm_path_uses_machine_profile(profile, tmp_path):
    creator = ProjectCreator(machine_profile=profile)
    path = creator.confirm_path("myapp")
    assert path.name == "myapp"
    assert str(tmp_path) in str(path)


def test_confirm_path_uses_type_routing(profile, tmp_path):
    creator = ProjectCreator(machine_profile=profile)
    ai_path = creator.confirm_path("brain", "ai")
    web_path = creator.confirm_path("site", "web")
    assert str(ai_path) != str(web_path)


# ── create: directory ─────────────────────────────────────────────────────────


def test_creates_directory(profile, tmp_path):
    creator = ProjectCreator(machine_profile=profile)
    proj = creator.create("testapp", base_dir=tmp_path / "projects")
    assert proj.is_dir()
    assert proj.name == "testapp"


def test_creates_orchid_subdirectory(profile, tmp_path):
    creator = ProjectCreator(machine_profile=profile)
    proj = creator.create("testapp", base_dir=tmp_path)
    assert (proj / ".orchid").is_dir()


# ── create: git init ──────────────────────────────────────────────────────────


def test_git_init_called(profile, tmp_path):
    profile.defaults["git_init"] = True
    creator = ProjectCreator(machine_profile=profile)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        proj = creator.create("gitapp", base_dir=tmp_path)
    calls = [c for c in mock_run.call_args_list if "git" in str(c)]
    assert any("init" in str(c) for c in calls)


def test_git_init_skipped_when_disabled(profile, tmp_path):
    profile.defaults["git_init"] = False
    creator = ProjectCreator(machine_profile=profile)
    with patch("subprocess.run") as mock_run:
        creator.create("nogitapp", base_dir=tmp_path)
    git_calls = [c for c in mock_run.call_args_list if "git" in str(c) and "init" in str(c)]
    assert len(git_calls) == 0


def test_git_init_skipped_when_git_already_exists(profile, tmp_path):
    project_dir = tmp_path / "existing"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()  # simulate existing repo

    profile.defaults["git_init"] = True
    creator = ProjectCreator(machine_profile=profile)
    with patch("subprocess.run") as mock_run:
        creator.create("existing", base_dir=tmp_path)
    git_calls = [c for c in mock_run.call_args_list if "init" in str(c)]
    assert len(git_calls) == 0


# ── create: orchid init templates ─────────────────────────────────────────────


def test_orchid_init_called(profile, tmp_path):
    creator = ProjectCreator(machine_profile=profile)
    proj = creator.create("myproject", description="A test project", base_dir=tmp_path)
    # templates should have been applied
    assert (proj / "CLAUDE.md").exists()
    assert (proj / "tasks.md").exists()
    assert (proj / ".orchid.yaml").exists()


def test_gitignore_created(profile, tmp_path):
    creator = ProjectCreator(machine_profile=profile)
    proj = creator.create("myproject", base_dir=tmp_path)
    gitignore = proj / ".gitignore"
    assert gitignore.exists()
    assert ".orchid/" in gitignore.read_text()


# ── create: lifecycle ─────────────────────────────────────────────────────────


def test_lifecycle_initialized_as_new(profile, tmp_path):
    creator = ProjectCreator(machine_profile=profile)
    proj = creator.create("newapp", base_dir=tmp_path)

    from orchid.lifecycle import ProjectLifecycle
    lc = ProjectLifecycle.load(proj)
    assert lc.current_phase() == "NEW"


def test_project_state_json_created(profile, tmp_path):
    creator = ProjectCreator(machine_profile=profile)
    proj = creator.create("stateapp", base_dir=tmp_path)
    assert (proj / ".orchid" / "project.state.json").exists()


def test_project_name_recorded_in_state(profile, tmp_path):
    creator = ProjectCreator(machine_profile=profile)
    proj = creator.create("namedapp", base_dir=tmp_path)

    from orchid.lifecycle import ProjectLifecycle
    lc = ProjectLifecycle.load(proj)
    assert lc.state.project_name == "namedapp"


# ── create: project_type annotation ──────────────────────────────────────────


def test_project_type_written_to_orchid_yaml(profile, tmp_path):
    creator = ProjectCreator(machine_profile=profile)
    proj = creator.create("webapp", project_type="web", base_dir=tmp_path)
    orchid_yaml = (proj / ".orchid.yaml").read_text()
    assert "project_type" in orchid_yaml
    assert "web" in orchid_yaml


def test_create_with_explicit_base_dir(tmp_path, profile):
    base = tmp_path / "custom_base"
    creator = ProjectCreator(machine_profile=profile)
    proj = creator.create("custom", base_dir=base)
    assert proj == (base / "custom").resolve()
    assert proj.is_dir()
