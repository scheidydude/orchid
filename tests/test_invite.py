"""Tests for admin-invite flow: POST /api/admin/invite, GET /api/auth/invite/{id},
POST /api/auth/invite/accept."""

import os
import pytest
from unittest.mock import patch


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def invite_client(tmp_path):
    """TestClient with an admin user and a regular user, no SMTP."""
    os.environ["JWT_SECRET"] = "test-jwt-secret-invite-tests"

    from orchid.interfaces.web_server import create_app
    import orchid.auth.store as store_mod
    from orchid.auth.store import FileUserStore
    from orchid.auth.jwt import hash_password
    from orchid.auth.types import User

    new_store = FileUserStore(path=tmp_path / "users.json")
    old_instance = store_mod._store_instance
    store_mod._store_instance = new_store

    admin = User(
        user_id="u_admin_invite",
        username="admin_user",
        email="admin@orchid.test",
        role="admin",
        password_hash=hash_password("admin-password"),
    )
    new_store.add_user(admin)

    app = create_app([])
    from starlette.testclient import TestClient
    admin_client = TestClient(app, raise_server_exceptions=True)

    # Log in as admin
    r = admin_client.post("/api/auth/login",
                          json={"username": "admin_user", "password": "admin-password"})
    assert r.status_code == 200, r.text

    yield admin_client, new_store, app

    store_mod._store_instance = old_instance


# ── Admin invite creation ─────────────────────────────────────────────────────

class TestAdminInvite:
    def test_admin_can_invite_user(self, invite_client):
        client, store, app = invite_client
        with patch("orchid.auth.mailer.is_configured", return_value=False):
            r = client.post("/api/admin/invite",
                            json={"email": "newuser@example.com", "role": "user"})
        assert r.status_code == 200
        body = r.json()
        assert "invite_url" in body
        assert "invite_id=" in body["invite_url"]
        assert "invite_token=" in body["invite_url"]
        assert body["email_sent"] is False  # SMTP not configured

    def test_invite_creates_inactive_user(self, invite_client):
        client, store, app = invite_client
        with patch("orchid.auth.mailer.is_configured", return_value=False):
            r = client.post("/api/admin/invite",
                            json={"email": "inactive@example.com", "role": "user"})
        assert r.status_code == 200
        user_id = r.json()["user_id"]
        user = store.get_user(user_id)
        assert user is not None
        assert user.is_active is False
        assert user.email == "inactive@example.com"

    def test_invite_duplicate_email_rejected(self, invite_client):
        client, store, app = invite_client
        with patch("orchid.auth.mailer.is_configured", return_value=False):
            client.post("/api/admin/invite", json={"email": "dup@example.com", "role": "user"})
            r2 = client.post("/api/admin/invite", json={"email": "dup@example.com", "role": "user"})
        assert r2.status_code == 409

    def test_invite_invalid_role_rejected(self, invite_client):
        client, store, app = invite_client
        with patch("orchid.auth.mailer.is_configured", return_value=False):
            r = client.post("/api/admin/invite",
                            json={"email": "bad@example.com", "role": "superuser"})
        assert r.status_code == 400

    def test_invite_requires_admin(self, invite_client, tmp_path):
        """A non-admin user cannot create invites."""
        client, store, app = invite_client
        from orchid.auth.types import User
        from orchid.auth.jwt import hash_password
        regular = User(
            user_id="u_regular",
            username="regular_user",
            email="regular@test.com",
            role="user",
            password_hash=hash_password("user-pass"),
        )
        store.add_user(regular)
        # Use a separate client logged in as regular user
        from starlette.testclient import TestClient
        c2 = TestClient(app, raise_server_exceptions=True)
        c2.post("/api/auth/login", json={"username": "regular_user", "password": "user-pass"})
        r = c2.post("/api/admin/invite", json={"email": "x@x.com"})
        assert r.status_code in (401, 403)

    def test_invite_missing_email_rejected(self, invite_client):
        client, store, app = invite_client
        with patch("orchid.auth.mailer.is_configured", return_value=False):
            r = client.post("/api/admin/invite", json={"role": "user"})
        assert r.status_code == 400

    def test_invite_email_sent_when_smtp_configured(self, invite_client):
        client, store, app = invite_client
        with patch("orchid.auth.mailer.is_configured", return_value=True), \
             patch("orchid.auth.mailer._send", return_value=True):
            r = client.post("/api/admin/invite",
                            json={"email": "smtp-test@example.com", "role": "user"})
        assert r.status_code == 200
        assert r.json()["email_sent"] is True


# ── Invite token validation ───────────────────────────────────────────────────

class TestInviteValidate:
    def _get_invite_parts(self, client, store):
        """Create an invite and return (token_id, secret) from URL."""
        with patch("orchid.auth.mailer.is_configured", return_value=False):
            r = client.post("/api/admin/invite",
                            json={"email": "validate@example.com", "role": "user"})
        assert r.status_code == 200
        url = r.json()["invite_url"]
        # Parse invite_id and invite_token from URL query string
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        return qs["invite_id"][0], qs["invite_token"][0]

    def test_valid_invite_returns_email(self, invite_client):
        client, store, app = invite_client
        token_id, secret = self._get_invite_parts(client, store)
        r = client.get(f"/api/auth/invite/{token_id}")
        assert r.status_code == 200
        assert r.json()["email"] == "validate@example.com"

    def test_invalid_token_id_404(self, invite_client):
        client, store, app = invite_client
        r = client.get("/api/auth/invite/inv_nonexistent12345")
        assert r.status_code == 404

    def test_expired_invite_410(self, invite_client):
        client, store, app = invite_client
        from datetime import UTC, timedelta, datetime
        token_id, secret = self._get_invite_parts(client, store)
        # Manually expire the invite
        inv = store.get_invite(token_id)
        inv.expires_at = datetime.now(UTC) - timedelta(hours=1)
        store.store_invite(inv)
        r = client.get(f"/api/auth/invite/{token_id}")
        assert r.status_code == 410


# ── Invite accept ─────────────────────────────────────────────────────────────

class TestInviteAccept:
    def _create_invite(self, client):
        """Create an invite and return (token_id, secret, email)."""
        email = "accept@example.com"
        with patch("orchid.auth.mailer.is_configured", return_value=False):
            r = client.post("/api/admin/invite", json={"email": email, "role": "user"})
        assert r.status_code == 200
        url = r.json()["invite_url"]
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        return qs["invite_id"][0], qs["invite_token"][0], email

    def test_accept_invite_activates_user(self, invite_client):
        client, store, app = invite_client
        token_id, secret, email = self._create_invite(client)
        r = client.post("/api/auth/invite/accept", json={
            "token_id": token_id,
            "invite_token": secret,
            "password": "newpassword123",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == email
        assert body["role"] == "user"

        user = store.get_user(body["user_id"])
        assert user.is_active is True
        assert user.password_hash is not None

    def test_accept_invite_issues_jwt_cookie(self, invite_client):
        client, store, app = invite_client
        token_id, secret, email = self._create_invite(client)
        r = client.post("/api/auth/invite/accept", json={
            "token_id": token_id,
            "invite_token": secret,
            "password": "newpassword123",
        })
        assert r.status_code == 200
        # orchid_access cookie should now be set
        assert "orchid_access" in client.cookies

    def test_accept_invite_marks_token_used(self, invite_client):
        client, store, app = invite_client
        token_id, secret, _ = self._create_invite(client)
        client.post("/api/auth/invite/accept", json={
            "token_id": token_id,
            "invite_token": secret,
            "password": "newpassword123",
        })
        inv = store.get_invite(token_id)
        assert inv.is_used is True

    def test_reuse_invite_rejected(self, invite_client):
        client, store, app = invite_client
        token_id, secret, _ = self._create_invite(client)
        client.post("/api/auth/invite/accept", json={
            "token_id": token_id,
            "invite_token": secret,
            "password": "newpassword123",
        })
        r2 = client.post("/api/auth/invite/accept", json={
            "token_id": token_id,
            "invite_token": secret,
            "password": "anotherpassword",
        })
        assert r2.status_code == 404

    def test_wrong_secret_rejected(self, invite_client):
        client, store, app = invite_client
        token_id, _, _ = self._create_invite(client)
        r = client.post("/api/auth/invite/accept", json={
            "token_id": token_id,
            "invite_token": "wrong-secret-aaaaaaa",
            "password": "newpassword123",
        })
        assert r.status_code == 401

    def test_password_too_short_rejected(self, invite_client):
        client, store, app = invite_client
        token_id, secret, _ = self._create_invite(client)
        r = client.post("/api/auth/invite/accept", json={
            "token_id": token_id,
            "invite_token": secret,
            "password": "short",
        })
        assert r.status_code == 400

    def test_missing_fields_rejected(self, invite_client):
        client, store, app = invite_client
        r = client.post("/api/auth/invite/accept", json={"token_id": "x"})
        assert r.status_code == 400

    def test_expired_invite_rejected(self, invite_client):
        client, store, app = invite_client
        token_id, secret, _ = self._create_invite(client)
        from datetime import UTC, timedelta, datetime
        inv = store.get_invite(token_id)
        inv.expires_at = datetime.now(UTC) - timedelta(hours=1)
        store.store_invite(inv)
        r = client.post("/api/auth/invite/accept", json={
            "token_id": token_id,
            "invite_token": secret,
            "password": "newpassword123",
        })
        assert r.status_code == 410
