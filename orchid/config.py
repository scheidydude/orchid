"""Config loader — merges orchid.config.yaml with .env overrides."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

_CONFIG_FILE = "orchid.config.yaml"
_ENV_PREFIX = "ORCHID_"


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


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path or _CONFIG_FILE)
    if not path.exists():
        # Walk up to find config relative to cwd
        for parent in Path.cwd().parents:
            candidate = parent / _CONFIG_FILE
            if candidate.exists():
                path = candidate
                break

    if not path.exists():
        return {}

    with open(path) as f:
        cfg: dict[str, Any] = yaml.safe_load(f) or {}

    return _expand_env(cfg)


# Module-level singleton
_config: dict[str, Any] | None = None


def get_config() -> dict[str, Any]:
    global _config
    if _config is None:
        _config = load_config()
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
