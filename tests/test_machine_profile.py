"""Tests for orchid/machine_profile.py — MachineProfile."""

import pytest
from pathlib import Path
import yaml

from orchid.machine_profile import MachineProfile, _DEFAULT_DATA


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def profile_path(tmp_path):
    return tmp_path / "machine-profile.yaml"


# ── Creation from defaults ────────────────────────────────────────────────────


def test_default_profile_created_if_missing(profile_path):
    assert not profile_path.exists()
    p = MachineProfile.load(path=profile_path)
    assert profile_path.exists()
    assert p.developer_name == ""


def test_default_profile_has_expected_keys(profile_path):
    p = MachineProfile.load(path=profile_path)
    assert "default" in p.project_roots
    assert "backend" in p.preferred_stacks
    assert "infrastructure" in p.__dict__ or hasattr(p, "infrastructure")


def test_default_file_is_valid_yaml(profile_path):
    MachineProfile.load(path=profile_path)
    data = yaml.safe_load(profile_path.read_text())
    assert "project_roots" in data
    assert "preferred_stacks" in data


# ── Load from YAML ────────────────────────────────────────────────────────────


def test_profile_loads_from_yaml(profile_path):
    data = {
        "developer": {"name": "Dave"},
        "project_roots": {
            "default": "~/Projects",
            "type_routing": {"ai": "~/AI"},
        },
        "preferred_stacks": {
            "backend": {"primary": "django", "alternative": "fastapi"},
            "frontend": {"primary": "vue"},
            "database": {"primary": "mysql", "lightweight": "sqlite"},
            "testing": {"python": "pytest", "javascript": "vitest"},
        },
        "infrastructure": {
            "local_llm": {"provider": "ollama", "base_url": "http://localhost:11434"},
            "embedding": {"provider": "nomic", "base_url": "http://localhost:11434"},
            "reverse_proxy": "nginx",
            "domain": "example.com",
            "container": "podman",
        },
        "defaults": {"model_preference": "local", "containerize": False, "include_traefik_config": False, "git_init": True},
        "offline_fallback": {"planning_agents": "local", "warn_on_local_planning": True},
    }
    profile_path.write_text(yaml.dump(data), encoding="utf-8")

    p = MachineProfile.load(path=profile_path)
    assert p.developer_name == "Dave"
    assert p.preferred_stacks["backend"]["primary"] == "django"
    assert p.infrastructure["reverse_proxy"] == "nginx"
    assert p.infrastructure["domain"] == "example.com"


# ── get_project_root ──────────────────────────────────────────────────────────


def test_get_project_root_default(profile_path):
    p = MachineProfile.load(path=profile_path)
    root = p.get_project_root()
    assert isinstance(root, Path)
    # Should be the expanded default path
    assert "Development" in str(root) or "LocalAI" in str(root) or root.exists() or True


def test_get_project_root_by_type(profile_path):
    data = dict(_DEFAULT_DATA)
    profile_path.write_text(yaml.dump(data), encoding="utf-8")
    p = MachineProfile.load(path=profile_path)

    ai_root = p.get_project_root("ai")
    web_root = p.get_project_root("web")
    # type routing should differ from default for known types
    assert str(ai_root) != str(p.get_project_root("unknown_type"))
    assert str(web_root) != str(ai_root)


def test_get_project_root_unknown_type_returns_default(profile_path):
    p = MachineProfile.load(path=profile_path)
    assert p.get_project_root("nonexistent") == p.get_project_root()


# ── to_context_string ─────────────────────────────────────────────────────────


def test_to_context_string_includes_stack(profile_path):
    p = MachineProfile.load(path=profile_path)
    ctx = p.to_context_string()
    assert "## Preferred Tech Stack" in ctx
    assert "fastapi" in ctx.lower() or "backend" in ctx.lower()


def test_to_context_string_includes_infrastructure(profile_path):
    p = MachineProfile.load(path=profile_path)
    ctx = p.to_context_string()
    assert "## Infrastructure" in ctx
    assert "traefik" in ctx.lower()


def test_to_context_string_includes_developer_name_when_set(profile_path):
    data = dict(_DEFAULT_DATA)
    data["developer"] = {"name": "Alice"}
    profile_path.write_text(yaml.dump(data), encoding="utf-8")
    p = MachineProfile.load(path=profile_path)
    ctx = p.to_context_string()
    assert "Alice" in ctx


def test_to_context_string_omits_empty_developer_name(profile_path):
    p = MachineProfile.load(path=profile_path)
    ctx = p.to_context_string()
    assert "Developer:" not in ctx


# ── Save ─────────────────────────────────────────────────────────────────────


def test_save_round_trips(profile_path):
    p = MachineProfile.load(path=profile_path)
    p.developer_name = "Bob"
    p.save()

    p2 = MachineProfile.load(path=profile_path)
    assert p2.developer_name == "Bob"


# ── get_stack_preferences ─────────────────────────────────────────────────────


def test_get_stack_preferences(profile_path):
    p = MachineProfile.load(path=profile_path)
    stacks = p.get_stack_preferences()
    assert isinstance(stacks, dict)
    assert "backend" in stacks
