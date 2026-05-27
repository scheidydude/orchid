"""Per-user credential vault backed by Fernet-encrypted JSON files (D0062).

Key derivation
--------------
    master_key  = ORCHID_VAULT_KEY env var (required when vault is accessed)
    per_user_key = HKDF-SHA256(master_key, salt=b"orchid-vault-v1", info=user_id.encode())
    fernet_key   = base64url(per_user_key[:32])

Each user gets a distinct Fernet key derived from the master key so that:
  - Compromising one user's key does not expose others'.
  - Rotating ORCHID_VAULT_KEY (after a breach) invalidates all vaults at once.
    Document this: when ORCHID_VAULT_KEY changes, users must re-enter credentials.

Storage
-------
    ~/.config/orchid/users/{user_id}/credentials.json.enc
    File content: raw Fernet token (bytes).
    Decrypted plaintext: JSON object mapping credential key → value (both str).

Credential keys are user-defined strings, e.g. "ANTHROPIC_API_KEY", "github_token".
Values are strings (secrets). Neither key names nor values are ever logged.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_USERS_DIR = Path.home() / ".config" / "orchid" / "users"


def _get_fernet(user_id: str):
    """Return a Fernet instance keyed for *user_id*.

    Raises RuntimeError if ORCHID_VAULT_KEY is not set.
    """
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    master_key = os.environ.get("ORCHID_VAULT_KEY", "").strip()
    if not master_key:
        raise RuntimeError(
            "ORCHID_VAULT_KEY not set — credential vault unavailable. "
            "Set ORCHID_VAULT_KEY in your environment to enable the vault."
        )

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"orchid-vault-v1",
        info=user_id.encode(),
    )
    key_bytes = hkdf.derive(master_key.encode())
    return Fernet(base64.urlsafe_b64encode(key_bytes))


class VaultStore:
    """Thread-safe per-user credential vault."""

    def __init__(self, users_dir: Path | None = None) -> None:
        self._dir = users_dir or _USERS_DIR
        self._lock = threading.Lock()

    def _path(self, user_id: str) -> Path:
        return self._dir / user_id / "credentials.json.enc"

    def _load(self, user_id: str) -> dict[str, str]:
        """Decrypt and return the credential map for *user_id*.

        Returns empty dict if no vault file exists.
        Raises RuntimeError if ORCHID_VAULT_KEY missing.
        Raises cryptography.fernet.InvalidToken if file is corrupt / key wrong.
        """
        path = self._path(user_id)
        if not path.exists():
            return {}
        f = _get_fernet(user_id)
        plaintext = f.decrypt(path.read_bytes())
        return json.loads(plaintext.decode("utf-8"))

    def _save(self, user_id: str, data: dict[str, str]) -> None:
        """Encrypt *data* and write to the vault file for *user_id*."""
        path = self._path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        f = _get_fernet(user_id)
        token = f.encrypt(json.dumps(data).encode("utf-8"))
        path.write_bytes(token)

    # ── Public API ─────────────────────────────────────────────────────────────

    def list_keys(self, user_id: str) -> list[str]:
        """Return credential key names for *user_id* (no values)."""
        with self._lock:
            return list(self._load(user_id).keys())

    def get(self, user_id: str, key: str) -> str | None:
        """Return the secret value for *key*, or None if not present."""
        with self._lock:
            return self._load(user_id).get(key)

    def set(self, user_id: str, key: str, value: str) -> None:
        """Store or update *key* → *value* in the vault."""
        with self._lock:
            data = self._load(user_id)
            data[key] = value
            self._save(user_id, data)

    def delete(self, user_id: str, key: str) -> bool:
        """Remove *key* from the vault. Returns True if it existed."""
        with self._lock:
            data = self._load(user_id)
            if key not in data:
                return False
            del data[key]
            self._save(user_id, data)
            return True

    def delete_all(self, user_id: str) -> None:
        """Wipe the entire vault for *user_id* (e.g. account deactivation)."""
        with self._lock:
            path = self._path(user_id)
            if path.exists():
                path.unlink()


# ── Singleton ──────────────────────────────────────────────────────────────────

_vault_instance: VaultStore | None = None
_vault_lock = threading.Lock()


def get_vault() -> VaultStore:
    """Return the process-wide VaultStore singleton."""
    global _vault_instance
    if _vault_instance is None:
        with _vault_lock:
            if _vault_instance is None:
                _vault_instance = VaultStore()
    return _vault_instance


def reset_vault() -> None:
    """Destroy the singleton (for tests only)."""
    global _vault_instance
    with _vault_lock:
        _vault_instance = None
