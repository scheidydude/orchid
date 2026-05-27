"""MCP server catalog for multi-user Orchid (Phase 3).

Admin-managed shared catalog
    ~/.config/orchid/mcp_catalog.json
    Format: {server_id: entry_dict}

User private servers
    ~/.config/orchid/users/{user_id}/mcp_servers.json
    Format: [{server_id, name, transport, ...}]

Access control rules (applied in order):
  1. scope=="admin-only"  → only role=="admin"
  2. user_id in allowed_users → grant (explicit override)
  3. user_role in allowed_roles → grant
  4. deny
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path.home() / ".config" / "orchid" / "mcp_catalog.json"
_USERS_DIR = Path.home() / ".config" / "orchid" / "users"


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class MCPServerEntry:
    """One MCP server in the admin-managed catalog."""

    server_id: str                    # e.g. "gmail", "filesystem"
    name: str
    transport: str                    # "stdio" | "http"
    config: dict = field(default_factory=dict)   # command/url/args — no secrets
    scope: str = "shared"             # "shared" | "private" | "admin-only"
    allowed_roles: list = field(default_factory=list)  # ["user","admin"]
    allowed_users: list = field(default_factory=list)  # explicit uid overrides
    requires_credential: str | None = None  # vault key the user must supply


def _entry_from_dict(d: dict) -> MCPServerEntry:
    return MCPServerEntry(
        server_id=d["server_id"],
        name=d.get("name", d["server_id"]),
        transport=d.get("transport", "stdio"),
        config=d.get("config") or {},
        scope=d.get("scope", "shared"),
        allowed_roles=list(d.get("allowed_roles") or []),
        allowed_users=list(d.get("allowed_users") or []),
        requires_credential=d.get("requires_credential") or None,
    )


def _user_can_access(entry: MCPServerEntry, user_id: str, user_role: str) -> bool:
    """True if user is allowed to use this server."""
    if entry.scope == "admin-only":
        return user_role == "admin"
    if user_id in entry.allowed_users:
        return True
    return user_role in entry.allowed_roles


def new_server_id() -> str:
    """Generate a short random server_id."""
    return f"srv_{uuid.uuid4().hex[:12]}"


# ── Admin catalog store ───────────────────────────────────────────────────────

class MCPCatalogStore:
    """Thread-safe JSON-backed catalog of admin-managed MCP servers."""

    def __init__(self, catalog_path: Path | None = None) -> None:
        self._path = catalog_path or _CATALOG_PATH
        self._lock = threading.Lock()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, MCPServerEntry]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("MCP catalog read error: %s", exc)
            return {}
        result: dict[str, MCPServerEntry] = {}
        for sid, entry_dict in raw.items():
            try:
                result[sid] = _entry_from_dict(entry_dict)
            except (KeyError, TypeError) as exc:
                logger.warning("Skipping malformed catalog entry %r: %s", sid, exc)
        return result

    def _save(self, catalog: dict[str, MCPServerEntry]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        raw = {sid: asdict(entry) for sid, entry in catalog.items()}
        self._path.write_text(json.dumps(raw, indent=2, default=str), "utf-8")

    # ── Read ─────────────────────────────────────────────────────────────────

    def list_servers(self) -> list[MCPServerEntry]:
        """Return all catalog entries (admin view, no access filtering)."""
        with self._lock:
            return list(self._load().values())

    def get_server(self, server_id: str) -> MCPServerEntry | None:
        """Return a single entry by server_id, or None."""
        with self._lock:
            return self._load().get(server_id)

    def get_servers_for_user(
        self, user_id: str, user_role: str
    ) -> list[MCPServerEntry]:
        """Return catalog entries accessible to this user."""
        with self._lock:
            return [
                e for e in self._load().values()
                if _user_can_access(e, user_id, user_role)
            ]

    # ── Write ────────────────────────────────────────────────────────────────

    def add_server(self, entry: MCPServerEntry) -> None:
        """Add a new server entry. Raises ValueError if server_id exists."""
        with self._lock:
            catalog = self._load()
            if entry.server_id in catalog:
                raise ValueError(f"MCP server '{entry.server_id}' already exists")
            catalog[entry.server_id] = entry
            self._save(catalog)

    def update_server(self, server_id: str, **kwargs: Any) -> MCPServerEntry:
        """Update fields on an existing entry. Returns updated entry."""
        _allowed = {
            "name", "transport", "config", "scope",
            "allowed_roles", "allowed_users", "requires_credential",
        }
        with self._lock:
            catalog = self._load()
            if server_id not in catalog:
                raise KeyError(f"MCP server '{server_id}' not found")
            entry = catalog[server_id]
            for k, v in kwargs.items():
                if k not in _allowed:
                    raise ValueError(f"Unknown field '{k}' on MCPServerEntry")
                setattr(entry, k, v)
            catalog[server_id] = entry
            self._save(catalog)
            return entry

    def delete_server(self, server_id: str) -> bool:
        """Remove an entry. Returns True if it existed."""
        with self._lock:
            catalog = self._load()
            if server_id not in catalog:
                return False
            del catalog[server_id]
            self._save(catalog)
            return True

    def grant_access(
        self,
        server_id: str,
        *,
        role: str | None = None,
        user_id: str | None = None,
    ) -> MCPServerEntry:
        """Add a role or user_id to the server's access lists."""
        with self._lock:
            catalog = self._load()
            if server_id not in catalog:
                raise KeyError(f"MCP server '{server_id}' not found")
            entry = catalog[server_id]
            if role and role not in entry.allowed_roles:
                entry.allowed_roles.append(role)
            if user_id and user_id not in entry.allowed_users:
                entry.allowed_users.append(user_id)
            catalog[server_id] = entry
            self._save(catalog)
            return entry

    def revoke_access(
        self,
        server_id: str,
        *,
        role: str | None = None,
        user_id: str | None = None,
    ) -> MCPServerEntry:
        """Remove a role or user_id from the server's access lists."""
        with self._lock:
            catalog = self._load()
            if server_id not in catalog:
                raise KeyError(f"MCP server '{server_id}' not found")
            entry = catalog[server_id]
            if role and role in entry.allowed_roles:
                entry.allowed_roles.remove(role)
            if user_id and user_id in entry.allowed_users:
                entry.allowed_users.remove(user_id)
            catalog[server_id] = entry
            self._save(catalog)
            return entry


# ── Singleton ─────────────────────────────────────────────────────────────────

_catalog_instance: MCPCatalogStore | None = None
_catalog_lock = threading.Lock()


def get_catalog() -> MCPCatalogStore:
    """Return the process-wide MCPCatalogStore singleton."""
    global _catalog_instance
    if _catalog_instance is None:
        with _catalog_lock:
            if _catalog_instance is None:
                _catalog_instance = MCPCatalogStore()
    return _catalog_instance


def reset_catalog() -> None:
    """Destroy the singleton (for tests only)."""
    global _catalog_instance
    with _catalog_lock:
        _catalog_instance = None


# ── Per-user private server store ─────────────────────────────────────────────

class UserMCPStore:
    """Per-user private MCP server definitions.

    File: ~/.config/orchid/users/{user_id}/mcp_servers.json
    Format: JSON array of server config dicts.

    Each dict must include:
        server_id   str   (unique per user; auto-assigned if missing)
        name        str
        transport   "stdio" | "http"
        command     str | list   (stdio only)
        url         str          (http only)
    """

    def __init__(self, users_dir: Path | None = None) -> None:
        self._dir = users_dir or _USERS_DIR
        self._lock = threading.Lock()

    def _path(self, user_id: str) -> Path:
        return self._dir / user_id / "mcp_servers.json"

    def _load(self, user_id: str) -> list[dict]:
        path = self._path(user_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text("utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, user_id: str, servers: list[dict]) -> None:
        path = self._path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(servers, indent=2), "utf-8")

    def list_servers(self, user_id: str) -> list[dict]:
        """Return all private server configs for the user."""
        with self._lock:
            return list(self._load(user_id))

    def get_server(self, user_id: str, server_id: str) -> dict | None:
        """Return a single server config, or None."""
        with self._lock:
            for s in self._load(user_id):
                if s.get("server_id") == server_id:
                    return s
            return None

    def add_server(self, user_id: str, config: dict) -> dict:
        """Add a private server config. Auto-assigns server_id if absent.

        Raises ValueError if server_id already exists for this user.
        """
        with self._lock:
            servers = self._load(user_id)
            config = dict(config)
            if "server_id" not in config:
                config["server_id"] = new_server_id()
            sid = config["server_id"]
            if any(s.get("server_id") == sid for s in servers):
                raise ValueError(f"Server '{sid}' already exists for this user")
            servers.append(config)
            self._save(user_id, servers)
            return config

    def delete_server(self, user_id: str, server_id: str) -> bool:
        """Remove a private server. Returns True if it existed."""
        with self._lock:
            servers = self._load(user_id)
            filtered = [s for s in servers if s.get("server_id") != server_id]
            if len(filtered) == len(servers):
                return False
            self._save(user_id, filtered)
            return True
