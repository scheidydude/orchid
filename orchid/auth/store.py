import dataclasses
import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from orchid.auth.types import AuthError, RefreshToken, User

logger = logging.getLogger(__name__)

_DATETIME_FIELDS_USER = {"created_at"}
_DATETIME_FIELDS_RT = {"expires_at", "created_at"}


def _parse_user(entry: dict) -> User:
    valid_keys = {f.name for f in dataclasses.fields(User)}
    filtered = {k: v for k, v in entry.items() if k in valid_keys}
    for key in _DATETIME_FIELDS_USER:
        if key in filtered and isinstance(filtered[key], str):
            filtered[key] = datetime.fromisoformat(filtered[key])
    return User(**filtered)


def _parse_refresh_token(entry: dict) -> RefreshToken:
    valid_keys = {f.name for f in dataclasses.fields(RefreshToken)}
    filtered = {k: v for k, v in entry.items() if k in valid_keys}
    for key in _DATETIME_FIELDS_RT:
        if key in filtered and isinstance(filtered[key], str):
            filtered[key] = datetime.fromisoformat(filtered[key])
    return RefreshToken(**filtered)


class UserStore:
    """Thread-safe, JSON-file-backed user and refresh-token store."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path.home() / ".config" / "orchid" / "users.json"
        self._lock = threading.Lock()
        self._users: dict[str, User] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for entry in data.get("users", []):
                try:
                    user = _parse_user(entry)
                    self._users[user.user_id] = user
                except Exception as exc:
                    logger.warning("Skipping malformed user entry: %s", exc)
            for entry in data.get("refresh_tokens", []):
                try:
                    rt = _parse_refresh_token(entry)
                    self._refresh_tokens[rt.token_id] = rt
                except Exception as exc:
                    logger.warning("Skipping malformed refresh token entry: %s", exc)
        except Exception as exc:
            logger.error("Failed to load store from %s: %s", self._path, exc)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "users": [dataclasses.asdict(u) for u in self._users.values()],
            "refresh_tokens": [dataclasses.asdict(rt) for rt in self._refresh_tokens.values()],
        }
        self._path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    # ── user CRUD ─────────────────────────────────────────────────────────────

    def add_user(self, user: User) -> None:
        with self._lock:
            if user.user_id in self._users:
                raise AuthError(f"User {user.user_id!r} already exists")
            self._users[user.user_id] = user
            self._save()

    def update_user(self, user: User) -> None:
        with self._lock:
            if user.user_id not in self._users:
                raise AuthError(f"User {user.user_id!r} not found")
            self._users[user.user_id] = user
            self._save()

    def remove_user(self, user_id: str) -> None:
        with self._lock:
            self._users.pop(user_id, None)
            self._save()

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            if user_id not in self._users:
                return False
            del self._users[user_id]
            self._save()
        return True

    def list_users(self) -> list[User]:
        with self._lock:
            return list(self._users.values())

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            return self._users.get(user_id)

    def get_by_id(self, user_id: str) -> User:
        with self._lock:
            user = self._users.get(user_id)
        if user is None:
            raise AuthError(f"User {user_id} not found")
        return user

    def get_by_token(self, token: str) -> User:
        """Legacy lookup by User.token field. Prefer JWT for new code."""
        with self._lock:
            for user in self._users.values():
                if user.token == token:
                    return user
        raise AuthError("Invalid token")

    def get_user_by_username(self, username: str) -> User | None:
        with self._lock:
            for u in self._users.values():
                if u.username == username:
                    return u
        return None

    # ── refresh token CRUD ────────────────────────────────────────────────────

    def store_refresh_token(self, rt: RefreshToken) -> None:
        with self._lock:
            self._refresh_tokens[rt.token_id] = rt
            self._save()

    def get_refresh_token(self, token_id: str) -> RefreshToken | None:
        with self._lock:
            return self._refresh_tokens.get(token_id)

    def revoke_refresh_token(self, token_id: str) -> None:
        with self._lock:
            rt = self._refresh_tokens.get(token_id)
            if rt:
                rt.is_revoked = True
                self._save()

    def revoke_all_refresh_tokens(self, user_id: str) -> None:
        with self._lock:
            for rt in self._refresh_tokens.values():
                if rt.user_id == user_id:
                    rt.is_revoked = True
            self._save()
