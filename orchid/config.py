"""Config loader — merges orchid.defaults.yaml with per-project .orchid.yaml."""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# 1. Walk up from cwd (default behaviour — works when invoked from inside the repo)
load_dotenv()
# 2. Orchid package root — works when running from source (parent.parent = repo root)
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=False)
# 3. Explicit repo location — works when installed via uv tool install, where
#    __file__ resolves to site-packages rather than the repo.
load_dotenv(dotenv_path=Path.home() / "LocalAI" / "orchid" / ".env", override=False)

# Orchid's own defaults live inside the package directory
_DEFAULTS_FILE = Path(__file__).parent / "orchid.defaults.yaml"

# User-level global config — merged over package defaults, under project configs.
# Lives at ~/.config/orchid/config.yaml (XDG, D0036).
_USER_CONFIG_FILE = Path.home() / ".config" / "orchid" / "config.yaml"

# Per-project override file (placed in the project root)
PROJECT_CONFIG_FILE = ".orchid.yaml"


def _expand_env(value: Any) -> Any:
    """Expand ${VAR:-default} patterns in string values."""
    if isinstance(value, str):
        def replacer(m: re.Match) -> str:
            var, _, default = m.group(1).partition(":-")
            return os.environ.get(var, default)
        return re.sub(r"\$\{([^}]+)\}", replacer, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_defaults() -> dict[str, Any]:
    """Load package defaults merged with the user-level global config.

    Layer order (lowest → highest priority):
      1. ``orchid.defaults.yaml``  — bundled package defaults
      2. ``~/.config/orchid/config.yaml`` — user global overrides (D0036)
    """
    base = _expand_env(_load_yaml(_DEFAULTS_FILE))
    user = _expand_env(_load_yaml(_USER_CONFIG_FILE))
    if user:
        return _deep_merge(base, user)
    return base


def load_project_config(project_dir: str | Path) -> dict[str, Any]:
    """Load a project's .orchid.yaml (returns {} if absent)."""
    return _expand_env(_load_yaml(Path(project_dir) / PROJECT_CONFIG_FILE))


def merge_for_project(project_dir: str | Path) -> dict[str, Any]:
    """Return merged config: defaults deep-merged with project overrides."""
    base = load_defaults()
    project = load_project_config(project_dir)

    # model_preference in project config overrides routing.default
    if "model_preference" in project:
        project.setdefault("routing", {})["default"] = project.pop("model_preference")

    return _deep_merge(base, project)


# ── Global singleton — reconfigured per project run ──────────────────────────

_config: dict[str, Any] | None = None


def configure_for_project(project_dir: str | Path) -> dict[str, Any]:
    """
    (Re)initialise the global config for a specific project directory.
    Must be called before any get() calls that depend on project settings.
    """
    global _config
    _config = merge_for_project(project_dir)
    return _config


def get_config() -> dict[str, Any]:
    global _config
    if _config is None:
        _config = load_defaults()
    return _config


def get(key_path: str, default: Any = None) -> Any:
    """Dot-separated key lookup, e.g. get('models.claude.model')."""
    cfg = get_config()
    parts = key_path.split(".")
    node: Any = cfg
    for part in parts:
        if not isinstance(node, dict):
            return default
        node = node.get(part)
        if node is None:
            return default
    return node


def get_mcp_servers() -> dict[str, Any]:
    """Return the MCP servers configuration dict (may be empty)."""
    return get("mcp_servers", {})