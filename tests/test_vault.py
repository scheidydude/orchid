"""Tests for orchid.vault.store (VaultStore) and /api/user/credentials + notifications."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch


# ── VaultStore unit tests ─────────────────────────────────────────────────────

class TestVaultStore:
    def setup_method(self):
        from orchid.vault.store import reset_vault
        reset_vault()

    def _store(self, tmp_path):
        from orchid.vault.store import VaultStore
        return VaultStore(users_dir=tmp_path)

    def test_no_vault_key_raises_on_write(self, tmp_path):
        """ORCHID_VAULT_KEY is required when writing credentials."""
        store = self._store(tmp_path)
        env = {k: v for k, v in os.environ.items() if k != "ORCHID_VAULT_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="ORCHID_VAULT_KEY"):
                store.set("user1", "KEY", "value")

    def test_no_vault_key_empty_vault_ok(self, tmp_path):
        """Empty vault returns [] without requiring ORCHID_VAULT_KEY."""
        store = self._store(tmp_path)
        env = {k: v for k, v in os.environ.items() if k != "ORCHID_VAULT_KEY"}
        with patch.dict(os.environ, env, clear=True):
            assert store.list_keys("user1") == []

    def test_empty_vault_returns_empty_list(self, tmp_path):
        store = self._store(tmp_path)
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "test-master-key-abc123"}):
            assert store.list_keys("user1") == []

    def test_set_and_list(self, tmp_path):
        store = self._store(tmp_path)
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "test-master-key-abc123"}):
            store.set("user1", "ANTHROPIC_API_KEY", "sk-secret-value")
            store.set("user1", "github_token", "ghp_xxxxxxx")
            keys = store.list_keys("user1")
            assert "ANTHROPIC_API_KEY" in keys
            assert "github_token" in keys
            assert len(keys) == 2

    def test_get_returns_value(self, tmp_path):
        store = self._store(tmp_path)
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "test-master-key-abc123"}):
            store.set("user1", "MY_KEY", "my-secret")
            assert store.get("user1", "MY_KEY") == "my-secret"

    def test_get_missing_key_returns_none(self, tmp_path):
        store = self._store(tmp_path)
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "test-master-key-abc123"}):
            assert store.get("user1", "NONEXISTENT") is None

    def test_delete_existing_key(self, tmp_path):
        store = self._store(tmp_path)
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "test-master-key-abc123"}):
            store.set("user1", "DEL_KEY", "value")
            deleted = store.delete("user1", "DEL_KEY")
            assert deleted is True
            assert store.get("user1", "DEL_KEY") is None

    def test_delete_missing_key_returns_false(self, tmp_path):
        store = self._store(tmp_path)
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "test-master-key-abc123"}):
            assert store.delete("user1", "NOT_THERE") is False

    def test_different_users_have_isolated_vaults(self, tmp_path):
        store = self._store(tmp_path)
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "test-master-key-abc123"}):
            store.set("alice", "SECRET", "alice-value")
            store.set("bob", "SECRET", "bob-value")
            assert store.get("alice", "SECRET") == "alice-value"
            assert store.get("bob", "SECRET") == "bob-value"
            # alice's vault file is different bytes from bob's
            alice_enc = (tmp_path / "alice" / "credentials.json.enc").read_bytes()
            bob_enc = (tmp_path / "bob" / "credentials.json.enc").read_bytes()
            assert alice_enc != bob_enc

    def test_encryption_at_rest(self, tmp_path):
        store = self._store(tmp_path)
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "test-master-key-abc123"}):
            store.set("user1", "SUPER_SECRET", "plaintext-value")
        raw = (tmp_path / "user1" / "credentials.json.enc").read_bytes()
        # The raw file must not contain the plaintext
        assert b"plaintext-value" not in raw
        assert b"SUPER_SECRET" not in raw

    def test_update_existing_key(self, tmp_path):
        store = self._store(tmp_path)
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "test-master-key-abc123"}):
            store.set("user1", "KEY", "v1")
            store.set("user1", "KEY", "v2")
            assert store.get("user1", "KEY") == "v2"
            assert len(store.list_keys("user1")) == 1

    def test_delete_all(self, tmp_path):
        store = self._store(tmp_path)
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "test-master-key-abc123"}):
            store.set("user1", "K1", "v1")
            store.set("user1", "K2", "v2")
            store.delete_all("user1")
            assert not (tmp_path / "user1" / "credentials.json.enc").exists()
            assert store.list_keys("user1") == []

    def test_wrong_vault_key_raises_on_read(self, tmp_path):
        store = self._store(tmp_path)
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "key-A"}):
            store.set("user1", "X", "secret")
        from cryptography.fernet import InvalidToken
        with patch.dict(os.environ, {"ORCHID_VAULT_KEY": "key-B"}):
            with pytest.raises(InvalidToken):
                store.list_keys("user1")


# ── Vault API tests ───────────────────────────────────────────────────────────

@pytest.fixture()
def vault_client(tmp_path):
    """TestClient with auth enabled, a real user, and vault key set."""
    import os
    os.environ["JWT_SECRET"] = "test-jwt-secret-for-vault-tests"
    os.environ["ORCHID_VAULT_KEY"] = "vault-master-key-for-tests"

    from orchid.interfaces.web_server import create_app
    import orchid.auth.store as store_mod
    from orchid.auth.store import FileUserStore
    from orchid.auth.jwt import hash_password
    from orchid.auth.types import User
    from orchid.vault.store import reset_vault, VaultStore
    import orchid.vault.store as vault_mod

    # Fresh auth store
    new_store = FileUserStore(path=tmp_path / "users.json")
    old_instance = store_mod._store_instance
    store_mod._store_instance = new_store

    # Fresh vault store pointing at tmp_path
    vault_mod._vault_instance = VaultStore(users_dir=tmp_path / "vaults")

    user = User(
        user_id="u_vault_test",
        username="vaultuser",
        email="vault@test.com",
        role="user",
        password_hash=hash_password("password123"),
    )
    new_store.add_user(user)

    app = create_app([])

    from starlette.testclient import TestClient
    client = TestClient(app, raise_server_exceptions=True)

    # Log in to get cookie
    r = client.post("/api/auth/login", json={"username": "vaultuser", "password": "password123"})
    assert r.status_code == 200, r.text
    # Cookie is now set on client

    yield client, user

    store_mod._store_instance = old_instance
    reset_vault()
    os.environ.pop("ORCHID_VAULT_KEY", None)


class TestVaultAPI:
    def test_list_credentials_empty(self, vault_client):
        client, user = vault_client
        r = client.get("/api/user/credentials")
        assert r.status_code == 200
        assert r.json() == {"keys": []}

    def test_set_and_list_credential(self, vault_client):
        client, user = vault_client
        r = client.put("/api/user/credentials/ANTHROPIC_API_KEY",
                       json={"value": "sk-test-12345"})
        assert r.status_code == 200
        assert r.json()["key"] == "ANTHROPIC_API_KEY"

        r2 = client.get("/api/user/credentials")
        assert r2.status_code == 200
        assert "ANTHROPIC_API_KEY" in r2.json()["keys"]

    def test_set_credential_value_not_returned_in_list(self, vault_client):
        client, user = vault_client
        client.put("/api/user/credentials/SECRET_KEY", json={"value": "super-secret"})
        r = client.get("/api/user/credentials")
        body = r.json()
        assert "super-secret" not in str(body)

    def test_delete_credential(self, vault_client):
        client, user = vault_client
        client.put("/api/user/credentials/TO_DELETE", json={"value": "bye"})
        r = client.delete("/api/user/credentials/TO_DELETE")
        assert r.status_code == 200
        r2 = client.get("/api/user/credentials")
        assert "TO_DELETE" not in r2.json()["keys"]

    def test_delete_nonexistent_credential_404(self, vault_client):
        client, user = vault_client
        r = client.delete("/api/user/credentials/GHOST")
        assert r.status_code == 404

    def test_set_credential_empty_value_rejected(self, vault_client):
        client, user = vault_client
        r = client.put("/api/user/credentials/EMPTY", json={"value": ""})
        assert r.status_code == 400

    def test_set_credential_invalid_key_rejected(self, vault_client):
        client, user = vault_client
        r = client.put("/api/user/credentials/bad key!", json={"value": "v"})
        assert r.status_code == 400

    def test_credentials_require_auth(self):
        import os
        os.environ["JWT_SECRET"] = "test-secret-for-auth-check"
        from orchid.interfaces.web_server import create_app
        app = create_app([])
        from starlette.testclient import TestClient
        client = TestClient(app, raise_server_exceptions=True)
        r = client.get("/api/user/credentials")
        assert r.status_code == 401


# ── Notification config API tests ─────────────────────────────────────────────

class TestNotificationConfigAPI:
    def test_get_empty_config(self, vault_client):
        client, user = vault_client
        r = client.get("/api/user/config/notifications")
        assert r.status_code == 200
        assert r.json() == {}

    def test_set_and_get_config(self, vault_client):
        client, user = vault_client
        payload = {
            "email_enabled": True,
            "email_address": "me@example.com",
            "notify_on_failure": True,
            "notify_on_success": False,
        }
        r = client.put("/api/user/config/notifications", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["email_enabled"] is True
        assert body["email_address"] == "me@example.com"

        r2 = client.get("/api/user/config/notifications")
        assert r2.json()["email_enabled"] is True

    def test_unknown_config_key_rejected(self, vault_client):
        client, user = vault_client
        r = client.put("/api/user/config/notifications", json={"bad_key": "oops"})
        assert r.status_code == 400

    def test_partial_update_merges(self, vault_client):
        client, user = vault_client
        client.put("/api/user/config/notifications", json={"email_enabled": True})
        client.put("/api/user/config/notifications", json={"telegram_enabled": True, "telegram_chat_id": "123"})
        r = client.get("/api/user/config/notifications")
        cfg = r.json()
        # Both keys should be present after two separate PUTs
        assert cfg.get("email_enabled") is True
        assert cfg.get("telegram_enabled") is True
