import dataclasses
import json
import logging
import threading
from pathlib import Path

from orchid.auth.types import User, AuthError

logger = logging.getLogger(__name__)


class UserStore:
    """Thread-safe, JSON-file-backed user store."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path.home() / ".config" / "orchid" / "users.json"
        self._lock = threading.Lock()
        self._users: dict[str, User] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for entry in data.get("users", []):
                # Strip keys not in the dataclass to avoid TypeError
                valid_keys = {f.name for f in dataclasses.fields(User)}
                filtered = {k: v for k, v in entry.items() if k in valid_keys}
                user = User(**filtered)
                self._users[user.user_id] = user
        except Exception as exc:
            logger.error("Failed to load users from %s: %s", self._path, exc)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"users": [dataclasses.asdict(u) for u in self._users.values()]}
        self._path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    # ── spec API ──────────────────────────────────────────────────────────────

    def get_by_token(self, token: str) -> User:
        with self._lock:
            for user in self._users.values():
                if user.token == token:
                    return user
        raise AuthError("Invalid token")

    def get_by_id(self, user_id: str) -> User:
        with self._lock:
            user = self._users.get(user_id)
        if user is None:
            raise AuthError(f"User {user_id} not found")
        return user

    def add_user(self, user: User) -> None:
        with self._lock:
            self._users[user.user_id] = user
            self._save()

    def remove_user(self, user_id: str) -> None:
        with self._lock:
            self._users.pop(user_id, None)
            self._save()

    # ── backward compat ───────────────────────────────────────────────────────

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            return self._users.get(user_id)

    def get_user_by_username(self, username: str) -> User | None:
        with self._lock:
            for u in self._users.values():
                if getattr(u, "username", "") == username:
                    return u
        return None

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            if user_id not in self._users:
                return False
            del self._users[user_id]
            self._save()
        return True
