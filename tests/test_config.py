"""Tests for config loading and project config merging."""

from __future__ import annotations

import yaml
from pathlib import Path
from orchid import config as cfg


def test_load_defaults_has_required_keys():
    defaults = cfg.load_defaults()
    assert "models" in defaults
    assert "routing" in defaults
    assert "memory" in defaults
    assert "agents" in defaults


def test_load_project_config_missing(tmp_path):
    """Returns empty dict when .orchid.yaml doesn't exist."""
    result = cfg.load_project_config(tmp_path)
    assert result == {}


def test_load_project_config_present(tmp_path):
    (tmp_path / ".orchid.yaml").write_text(
        "project: myapp\ndescription: Test project\nmodel_preference: claude\n"
    )
    result = cfg.load_project_config(tmp_path)
    assert result["project"] == "myapp"
    assert result["model_preference"] == "claude"


def test_merge_for_project_applies_model_preference(tmp_path):
    (tmp_path / ".orchid.yaml").write_text("model_preference: claude\n")
    merged = cfg.merge_for_project(tmp_path)
    assert merged["routing"]["default"] == "claude"


def test_merge_for_project_deep_merges_memory(tmp_path):
    (tmp_path / ".orchid.yaml").write_text("memory:\n  compression_threshold: 9999\n")
    merged = cfg.merge_for_project(tmp_path)
    # Project override applied
    assert merged["memory"]["compression_threshold"] == 9999
    # Defaults preserved
    assert merged["memory"]["hot_memory_file"] == "CLAUDE.md"


def test_merge_for_project_no_config_uses_defaults(tmp_path):
    merged = cfg.merge_for_project(tmp_path)
    defaults = cfg.load_defaults()
    assert merged["routing"]["default"] == defaults["routing"]["default"]


def test_configure_for_project_updates_singleton(tmp_path):
    (tmp_path / ".orchid.yaml").write_text("memory:\n  compression_threshold: 1234\n")
    cfg.configure_for_project(tmp_path)
    assert cfg.get("memory.compression_threshold") == 1234
    # Reset for other tests
    cfg.configure_for_project(Path("."))
