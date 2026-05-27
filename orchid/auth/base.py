"""BaseUserStore ABC — implemented by FileUserStore and PostgresUserStore."""

from __future__ import annotations

from abc import ABC, abstractmethod

from orchid.auth.types import ApiKey, InviteToken, OAuthAccount, RefreshToken, User


class BaseUserStore(ABC):

    # ── users ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def add_user(self, user: User) -> None: ...

    @abstractmethod
    def update_user(self, user: User) -> None: ...

    @abstractmethod
    def remove_user(self, user_id: str) -> None: ...

    @abstractmethod
    def delete_user(self, user_id: str) -> bool: ...

    @abstractmethod
    def list_users(self) -> list[User]: ...

    @abstractmethod
    def get_user(self, user_id: str) -> User | None: ...

    @abstractmethod
    def get_by_id(self, user_id: str) -> User: ...

    @abstractmethod
    def get_by_token(self, token: str) -> User: ...

    @abstractmethod
    def get_user_by_username(self, username: str) -> User | None: ...

    @abstractmethod
    def get_user_by_email(self, email: str) -> User | None: ...

    # ── refresh tokens ────────────────────────────────────────────────────────

    @abstractmethod
    def store_refresh_token(self, rt: RefreshToken) -> None: ...

    @abstractmethod
    def get_refresh_token(self, token_id: str) -> RefreshToken | None: ...

    @abstractmethod
    def revoke_refresh_token(self, token_id: str) -> None: ...

    @abstractmethod
    def revoke_all_refresh_tokens(self, user_id: str) -> None: ...

    # ── API keys ──────────────────────────────────────────────────────────────

    @abstractmethod
    def store_api_key(self, key: ApiKey) -> None: ...

    @abstractmethod
    def get_api_key(self, key_id: str) -> ApiKey | None: ...

    @abstractmethod
    def list_api_keys(self, user_id: str) -> list[ApiKey]: ...

    @abstractmethod
    def revoke_api_key(self, key_id: str) -> bool: ...

    @abstractmethod
    def touch_api_key(self, key_id: str) -> None: ...

    # ── OAuth accounts ────────────────────────────────────────────────────────

    @abstractmethod
    def store_oauth_account(self, oa: OAuthAccount) -> None: ...

    @abstractmethod
    def get_oauth_account(self, provider: str, provider_user_id: str) -> OAuthAccount | None: ...

    @abstractmethod
    def list_oauth_accounts_for_user(self, user_id: str) -> list[OAuthAccount]: ...

    # ── scheduled tasks ───────────────────────────────────────────────────────

    @abstractmethod
    def upsert_scheduled_task(self, user_id: str, task_data: dict) -> None:
        """Create or replace a scheduled task for *user_id*. *task_data* is a dict."""
        ...

    @abstractmethod
    def get_scheduled_task(self, user_id: str, task_id: str) -> dict | None:
        """Return the stored dict for one scheduled task, or ``None``."""
        ...

    @abstractmethod
    def delete_scheduled_task(self, user_id: str, task_id: str) -> bool:
        """Remove a scheduled task. Returns ``True`` if it existed."""
        ...

    @abstractmethod
    def get_all_enabled_scheduled_tasks(self) -> list[tuple[str, dict]]:
        """Return all enabled scheduled tasks across all users as ``(user_id, task_dict)`` tuples."""
        ...

    # ── invite tokens ─────────────────────────────────────────────────────────

    @abstractmethod
    def store_invite(self, invite: InviteToken) -> None:
        """Persist an invite token."""
        ...

    @abstractmethod
    def get_invite(self, token_id: str) -> InviteToken | None:
        """Return an invite by token_id, or None if not found."""
        ...

    @abstractmethod
    def mark_invite_used(self, token_id: str) -> None:
        """Mark an invite token as used (idempotent)."""
        ...
