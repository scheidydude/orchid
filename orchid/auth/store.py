import dataclasses
import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from orchid.auth.types import ApiKey, AuthError, OAuthAccount, RefreshToken, User

logger = logging.getLogger(__name__)

_DATETIME_FIELDS_USER = {"created_at"}
_DATETIME_FIELDS_RT = {"expires_at", "created_at"}
_DATETIME_FIELDS_AK = {"created_at", "last_used", "expires_at"}
_DATETIME_FIELDS_OA = {"expires_at", "created_at"}


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


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


def _parse_oauth_account(entry: dict) -> OAuthAccount:
    valid_keys = {f.name for f in dataclasses.fields(OAuthAccount)}
    filtered = {k: v for k, v in entry.items() if k in valid_keys}
    for key in _DATETIME_FIELDS_OA:
        if key in filtered and isinstance(filtered[key], str):
            filtered[key] = _parse_datetime(filtered[key])
    return OAuthAccount(**filtered)


def _parse_api_key(entry: dict) -> ApiKey:
    valid_keys = {f.name for f in dataclasses.fields(ApiKey)}
    filtered = {k: v for k, v in entry.items() if k in valid_keys}
    for key in _DATETIME_FIELDS_AK:
        if key in filtered and isinstance(filtered[key], str):
            filtered[key] = _parse_datetime(filtered[key])
    return ApiKey(**filtered)


class UserStore:
    """Thread-safe, JSON-file-backed user and refresh-token store."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path.home() / ".config" / "orchid" / "users.json"
        self._lock = threading.Lock()
        self._users: dict[str, User] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        self._api_keys: dict[str, ApiKey] = {}
        self._oauth_accounts: dict[str, OAuthAccount] = {}  # key: "{provider}:{provider_user_id}"
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
            for entry in data.get("api_keys", []):
                try:
                    ak = _parse_api_key(entry)
                    self._api_keys[ak.key_id] = ak
                except Exception as exc:
                    logger.warning("Skipping malformed API key entry: %s", exc)
            for entry in data.get("oauth_accounts", []):
                try:
                    oa = _parse_oauth_account(entry)
                    self._oauth_accounts[f"{oa.provider}:{oa.provider_user_id}"] = oa
                except Exception as exc:
                    logger.warning("Skipping malformed OAuth account entry: %s", exc)
        except Exception as exc:
            logger.error("Failed to load store from %s: %s", self._path, exc)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "users": [dataclasses.asdict(u) for u in self._users.values()],
            "refresh_tokens": [dataclasses.asdict(rt) for rt in self._refresh_tokens.values()],
            "api_keys": [dataclasses.asdict(ak) for ak in self._api_keys.values()],
            "oauth_accounts": [dataclasses.asdict(oa) for oa in self._oauth_accounts.values()],
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

    def get_user_by_email(self, email: str) -> User | None:
        with self._lock:
            for u in self._users.values():
                if u.email and u.email.lower() == email.lower():
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

    # ── API key CRUD ──────────────────────────────────────────────────────────

    def store_api_key(self, key: ApiKey) -> None:
        with self._lock:
            self._api_keys[key.key_id] = key
            self._save()

    def get_api_key(self, key_id: str) -> ApiKey | None:
        with self._lock:
            return self._api_keys.get(key_id)

    def list_api_keys(self, user_id: str) -> list[ApiKey]:
        with self._lock:
            return [k for k in self._api_keys.values() if k.user_id == user_id]

    def revoke_api_key(self, key_id: str) -> bool:
        with self._lock:
            key = self._api_keys.get(key_id)
            if key is None:
                return False
            key.is_active = False
            self._save()
        return True

    def touch_api_key(self, key_id: str) -> None:
        """Update last_used timestamp. Called on every successful API key auth."""
        with self._lock:
            key = self._api_keys.get(key_id)
            if key:
                key.last_used = datetime.now()
                self._save()

    # ── OAuth account CRUD ────────────────────────────────────────────────────

    def store_oauth_account(self, oa: OAuthAccount) -> None:
        with self._lock:
            self._oauth_accounts[f"{oa.provider}:{oa.provider_user_id}"] = oa
            self._save()

    def get_oauth_account(self, provider: str, provider_user_id: str) -> OAuthAccount | None:
        with self._lock:
            return self._oauth_accounts.get(f"{provider}:{provider_user_id}")

    def list_oauth_accounts_for_user(self, user_id: str) -> list[OAuthAccount]:
        with self._lock:
            return [oa for oa in self._oauth_accounts.values() if oa.user_id == user_id]
