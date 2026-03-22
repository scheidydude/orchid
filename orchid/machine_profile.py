"""Machine profile — homelab/developer preferences for project creation.

Lives at ~/.config/orchid/machine-profile.yaml (XDG config dir).
Created with defaults if missing. Injected into strategic agent prompts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROFILE_PATH = Path("~/.config/orchid/machine-profile.yaml").expanduser()

_DEFAULT_DATA: dict = {
    "developer": {
        "name": "",
    },
    "project_roots": {
        "default": "~/Documents/Development",
        "ai_projects": "~/LocalAI",
        "type_routing": {
            "ai": "~/LocalAI",
            "web": "~/Documents/Development",
            "tool": "~/LocalAI",
            "game": "~/Documents/Development",
        },
    },
    "preferred_stacks": {
        "backend": {
            "primary": "fastapi",
            "alternative": "nodejs-express",
        },
        "frontend": {
            "primary": "react",
            "alternative": "vanilla-js",
        },
        "database": {
            "primary": "postgresql",
            "lightweight": "sqlite",
        },
        "testing": {
            "python": "pytest",
            "javascript": "jest",
        },
    },
    "infrastructure": {
        "local_llm": {
            "provider": "llama.cpp",
            "base_url": "http://localhost:8080/v1",
        },
        "embedding": {
            "provider": "nomic-embed-text",
            "base_url": "http://localhost:8081/v1",
        },
        "reverse_proxy": "traefik",
        "domain": "",
        "container": "docker",
    },
    "defaults": {
        "model_preference": "auto",
        "containerize": True,
        "include_traefik_config": True,
        "git_init": True,
    },
    "offline_fallback": {
        "planning_agents": "local",
        "warn_on_local_planning": True,
    },
}


@dataclass
class MachineProfile:
    developer_name: str
    project_roots: dict
    preferred_stacks: dict
    infrastructure: dict
    defaults: dict
    offline_fallback: dict
    _path: Path = field(default_factory=lambda: PROFILE_PATH, repr=False)

    @classmethod
    def load(cls, path: Path | None = None) -> MachineProfile:
        profile_path = Path(path) if path else PROFILE_PATH
        if not profile_path.exists():
            logger.info("No machine profile at %s — writing defaults", profile_path)
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(
                yaml.dump(_DEFAULT_DATA, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
            data: dict = _DEFAULT_DATA
        else:
            data = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}

        def _get(key: str) -> dict:
            return data.get(key, _DEFAULT_DATA[key])

        return cls(
            developer_name=data.get("developer", {}).get("name", ""),
            project_roots=_get("project_roots"),
            preferred_stacks=_get("preferred_stacks"),
            infrastructure=_get("infrastructure"),
            defaults=_get("defaults"),
            offline_fallback=_get("offline_fallback"),
            _path=profile_path,
        )

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "developer": {"name": self.developer_name},
            "project_roots": self.project_roots,
            "preferred_stacks": self.preferred_stacks,
            "infrastructure": self.infrastructure,
            "defaults": self.defaults,
            "offline_fallback": self.offline_fallback,
        }
        self._path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    def get_project_root(self, project_type: str | None = None) -> Path:
        """Return the recommended root directory for a project type."""
        type_routing = self.project_roots.get("type_routing", {})
        if project_type and project_type in type_routing:
            return Path(type_routing[project_type]).expanduser()
        return Path(self.project_roots.get("default", "~/Documents/Development")).expanduser()

    def get_stack_preferences(self) -> dict:
        return self.preferred_stacks

    def to_context_string(self) -> str:
        """Format profile as a compact context block for agent prompts."""
        stacks = self.preferred_stacks
        infra = self.infrastructure
        lines: list[str] = []

        if self.developer_name:
            lines.append(f"Developer: {self.developer_name}")

        lines.append("## Preferred Tech Stack")
        backend = stacks.get("backend", {})
        if backend:
            alt = backend.get("alternative", "")
            lines.append(
                f"- Backend: {backend.get('primary', 'fastapi')}"
                + (f" (alt: {alt})" if alt else "")
            )
        frontend = stacks.get("frontend", {})
        if frontend:
            alt = frontend.get("alternative", "")
            lines.append(
                f"- Frontend: {frontend.get('primary', 'react')}"
                + (f" (alt: {alt})" if alt else "")
            )
        db = stacks.get("database", {})
        if db:
            lines.append(
                f"- Database: {db.get('primary', 'postgresql')}"
                f" (lightweight: {db.get('lightweight', 'sqlite')})"
            )
        testing = stacks.get("testing", {})
        if testing:
            lines.append(
                f"- Testing: Python={testing.get('python', 'pytest')},"
                f" JS={testing.get('javascript', 'jest')}"
            )

        lines.append("## Infrastructure")
        lines.append(f"- Reverse proxy: {infra.get('reverse_proxy', 'traefik')}")
        domain = infra.get("domain", "")
        if domain:
            lines.append(f"- Domain: {domain}")
        lines.append(f"- Container: {infra.get('container', 'docker')}")

        defs = self.defaults
        flags = []
        if defs.get("git_init", True):
            flags.append("git-init")
        if defs.get("containerize", True):
            flags.append("containerize")
        if defs.get("include_traefik_config", True):
            flags.append("traefik-config")
        if flags:
            lines.append(f"- Defaults: {', '.join(flags)}")

        return "\n".join(lines)
