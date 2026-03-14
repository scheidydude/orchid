"""Tests for orchid init command."""

from __future__ import annotations

from pathlib import Path
from typer.testing import CliRunner
from orchid.interfaces.cli import app

runner = CliRunner()


def test_init_creates_files(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path), "--name", "myapp", "--description", "My test app"])
    assert result.exit_code == 0, result.output

    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / "tasks.md").exists()
    assert (tmp_path / ".orchid.yaml").exists()
    assert (tmp_path / ".gitignore").exists()


def test_init_substitutes_project_name(tmp_path):
    runner.invoke(app, ["init", str(tmp_path), "--name", "webtron"])
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "webtron" in content
    content = (tmp_path / ".orchid.yaml").read_text()
    assert "webtron" in content


def test_init_adds_orchid_to_gitignore(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    gi = (tmp_path / ".gitignore").read_text()
    assert ".orchid/" in gi


def test_init_appends_to_existing_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")
    runner.invoke(app, ["init", str(tmp_path)])
    gi = (tmp_path / ".gitignore").read_text()
    assert "*.pyc" in gi
    assert ".orchid/" in gi


def test_init_skips_existing_without_force(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# My existing content\n")
    runner.invoke(app, ["init", str(tmp_path)])
    # Original content should be preserved
    assert "My existing content" in (tmp_path / "CLAUDE.md").read_text()


def test_init_force_overwrites(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Old content\n")
    runner.invoke(app, ["init", str(tmp_path), "--name", "newproject", "--force"])
    content = (tmp_path / "CLAUDE.md").read_text()
    assert "Old content" not in content
    assert "newproject" in content
