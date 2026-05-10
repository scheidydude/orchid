"""BaseUserStore ABC — implemented by FileUserStore and PostgresUserStore."""

from __future__ import annotations

from abc import ABC, abstractmethod

from orchid.auth.types import ApiKey, OAuthAccount, RefreshToken, User


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
