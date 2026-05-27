"""Project ownership registry.

Tracks which Orchid user owns each project, enabling:
- user-scoped project listing: ``list_projects(user_id=...)``
- automatic path routing to ``~/.config/orchid/projects/{uid}/``
- ownership audit trail

Storage: ``~/.config/orchid/projects/registry.json``
Format: ``{project_id: {project_id, project_path, owner_id, created_at}}``

Owner semantics:
- ``owner_id = ""``  — system/admin project (no user owner)
- ``owner_id = uid`` — created by that user; default path under their namespace
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path("~/.config/orchid/projects/registry.json").expanduser()


@dataclass
class ProjectEntry:
    """Ownership record for one registered project."""

    project_id: str
    project_path: str
    owner_id: str   # user_id; empty string = system/admin project
    created_at: str  # ISO-8601 UTC


def user_project_base(user_id: str) -> Path:
    """Return ``~/.config/orchid/projects/{user_id}/`` — default base for user projects."""
    return Path.home() / ".config" / "orchid" / "projects" / user_id


class ProjectRegistry:
    """Thread-safe JSON-backed project ownership store."""

    def __init__(self, registry_path: Path | None = None) -> None:
        self._path = registry_path or _REGISTRY_PATH
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    # ── I/O ───────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                with self._lock:
                    self._data = raw if isinstance(raw, dict) else {}
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load project registry: %s", exc)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            snapshot = dict(self._data)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
            os.replace(tmp, self._path)
        except Exception as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            logger.warning("Failed to save project registry: %s", exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def register(
        self,
        project_id: str,
        project_path: str,
        owner_id: str = "",
    ) -> ProjectEntry:
        """Record ownership of a project.

        Idempotent — preserves ``created_at`` on re-registration; updates
        ``project_path`` and ``owner_id`` to reflect the latest values.
        """
        now = datetime.now(UTC).isoformat()
        with self._lock:
            existing = self._data.get(project_id)
            entry = ProjectEntry(
                project_id=project_id,
                project_path=str(project_path),
                owner_id=owner_id,
                created_at=existing["created_at"] if existing else now,
            )
            self._data[project_id] = asdict(entry)
        self._save()
        logger.debug("Registered project %s owner=%r", project_id, owner_id)
        return entry

    def unregister(self, project_id: str) -> bool:
        """Remove a project from the registry. Returns True if it existed."""
        with self._lock:
            existed = project_id in self._data
            self._data.pop(project_id, None)
        if existed:
            self._save()
            logger.debug("Unregistered project %s", project_id)
        return existed

    def get(self, project_id: str) -> ProjectEntry | None:
        """Return the ProjectEntry for a project, or None if not registered."""
        with self._lock:
            raw = self._data.get(project_id)
        if raw is None:
            return None
        return ProjectEntry(
            project_id=raw["project_id"],
            project_path=raw["project_path"],
            owner_id=raw.get("owner_id", ""),
            created_at=raw.get("created_at", ""),
        )

    def get_owner(self, project_id: str) -> str | None:
        """Return owner_id, or None if the project is not in the registry."""
        entry = self.get(project_id)
        return entry.owner_id if entry is not None else None

    def list_projects(self, user_id: str | None = None) -> list[ProjectEntry]:
        """Return registered projects, optionally filtered by owner.

        Args:
            user_id: When provided, return only projects where
                     ``owner_id == user_id``. Pass ``None`` for all projects.
        """
        with self._lock:
            rows = list(self._data.values())
        entries = [
            ProjectEntry(
                project_id=r["project_id"],
                project_path=r["project_path"],
                owner_id=r.get("owner_id", ""),
                created_at=r.get("created_at", ""),
            )
            for r in rows
        ]
        if user_id is not None:
            entries = [e for e in entries if e.owner_id == user_id]
        return entries


# ── Singleton ─────────────────────────────────────────────────────────────────

_registry_instance: ProjectRegistry | None = None
_registry_lock = threading.Lock()


def get_registry(registry_path: Path | None = None) -> ProjectRegistry:
    """Return the module-level singleton ProjectRegistry."""
    global _registry_instance
    with _registry_lock:
        if _registry_instance is None:
            _registry_instance = ProjectRegistry(registry_path)
        return _registry_instance


def reset_registry() -> None:
    """Reset the singleton. Use in tests only."""
    global _registry_instance
    with _registry_lock:
        _registry_instance = None
