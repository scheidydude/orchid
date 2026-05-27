"""Tests for project ownership registry (orchid.projects.registry).

Covers:
- register / list_projects / get_owner / unregister
- user_project_base() path helper
- GET /api/projects D0060 filtering
- POST /api/projects ownership recording + user-namespace path
- _unregister_project propagates to registry
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


# ══════════════════════════════════════════════════════════════════════════════
# ProjectRegistry unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestProjectRegistry:

    def _make_registry(self, tmp_path: Path):
        from orchid.projects.registry import ProjectRegistry
        return ProjectRegistry(registry_path=tmp_path / "registry.json")

    def test_register_and_get(self, tmp_path):
        reg = self._make_registry(tmp_path)
        entry = reg.register("proj1", "/home/alice/proj1", owner_id="alice")
        assert entry.project_id == "proj1"
        assert entry.project_path == "/home/alice/proj1"
        assert entry.owner_id == "alice"
        assert entry.created_at  # non-empty

    def test_get_returns_none_for_unknown(self, tmp_path):
        reg = self._make_registry(tmp_path)
        assert reg.get("nonexistent") is None

    def test_get_owner(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("p1", "/p1", owner_id="alice")
        assert reg.get_owner("p1") == "alice"
        assert reg.get_owner("unknown") is None

    def test_list_projects_no_filter(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("p1", "/p1", owner_id="alice")
        reg.register("p2", "/p2", owner_id="bob")
        reg.register("p3", "/p3", owner_id="")
        entries = reg.list_projects()
        ids = {e.project_id for e in entries}
        assert ids == {"p1", "p2", "p3"}

    def test_list_projects_filtered_by_user(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("p1", "/p1", owner_id="alice")
        reg.register("p2", "/p2", owner_id="bob")
        reg.register("p3", "/p3", owner_id="alice")
        entries = reg.list_projects(user_id="alice")
        ids = {e.project_id for e in entries}
        assert ids == {"p1", "p3"}
        assert all(e.owner_id == "alice" for e in entries)

    def test_list_projects_user_with_no_projects(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("p1", "/p1", owner_id="alice")
        entries = reg.list_projects(user_id="charlie")
        assert entries == []

    def test_unregister(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("p1", "/p1", owner_id="alice")
        assert reg.unregister("p1") is True
        assert reg.get("p1") is None

    def test_unregister_nonexistent_returns_false(self, tmp_path):
        reg = self._make_registry(tmp_path)
        assert reg.unregister("ghost") is False

    def test_register_idempotent_preserves_created_at(self, tmp_path):
        reg = self._make_registry(tmp_path)
        e1 = reg.register("p1", "/p1", owner_id="alice")
        e2 = reg.register("p1", "/p1-new", owner_id="alice")
        assert e2.created_at == e1.created_at  # preserved
        assert e2.project_path == "/p1-new"    # updated

    def test_persists_to_disk(self, tmp_path):
        from orchid.projects.registry import ProjectRegistry
        reg_path = tmp_path / "registry.json"
        reg1 = ProjectRegistry(registry_path=reg_path)
        reg1.register("p1", "/p1", owner_id="alice")

        reg2 = ProjectRegistry(registry_path=reg_path)
        entry = reg2.get("p1")
        assert entry is not None
        assert entry.owner_id == "alice"

    def test_empty_owner_id_is_system_project(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.register("sys", "/sys")
        entry = reg.get("sys")
        assert entry.owner_id == ""


# ══════════════════════════════════════════════════════════════════════════════
# user_project_base helper
# ══════════════════════════════════════════════════════════════════════════════

def test_user_project_base():
    from orchid.projects.registry import user_project_base
    base = user_project_base("alice")
    assert str(base).endswith("/.config/orchid/projects/alice")
    assert base == Path.home() / ".config" / "orchid" / "projects" / "alice"


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

def test_get_registry_singleton(tmp_path):
    from orchid.projects.registry import get_registry, reset_registry
    reset_registry()
    try:
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2
    finally:
        reset_registry()


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/projects — D0060 filtering
# ══════════════════════════════════════════════════════════════════════════════

class TestGetProjectsFiltering:

    def _make_app(self, tmp_path, projects, user):
        import pytest
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        import orchid.auth.middleware as mw
        from orchid.projects.registry import ProjectRegistry, reset_registry
        from orchid.interfaces.web_server import create_app

        reset_registry()

        async def _fake_optional():
            return user

        reg = ProjectRegistry(registry_path=tmp_path / "registry.json")

        with patch.object(mw, "get_optional_user", _fake_optional), \
             patch("orchid.interfaces.web_server.get_optional_user", _fake_optional), \
             patch("orchid.projects.registry.get_registry", return_value=reg):
            app = create_app(
                project_paths=[str(p) for p in projects],
                enable_telegram=False,
                enable_slack=False,
            )
            client = TestClient(app, raise_server_exceptions=True)
            return client

    def test_admin_sees_all_projects(self, tmp_path):
        import pytest
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from orchid.auth.types import User

        p1 = tmp_path / "proj1"
        p1.mkdir()
        p2 = tmp_path / "proj2"
        p2.mkdir()

        admin = User(user_id="admin", username="admin", role="admin", is_active=True)
        client = self._make_app(tmp_path, [p1, p2], admin)
        r = client.get("/api/projects")
        assert r.status_code == 200
        ids = {p["id"] for p in r.json()}
        assert "proj1" in ids
        assert "proj2" in ids

    def test_user_with_empty_projects_sees_all(self, tmp_path):
        import pytest
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from orchid.auth.types import User

        p1 = tmp_path / "alpha"
        p1.mkdir()
        p2 = tmp_path / "beta"
        p2.mkdir()

        # empty projects = unrestricted
        user = User(user_id="bob", username="bob", role="user", is_active=True, projects=[])
        client = self._make_app(tmp_path, [p1, p2], user)
        r = client.get("/api/projects")
        assert r.status_code == 200
        ids = {p["id"] for p in r.json()}
        assert "alpha" in ids
        assert "beta" in ids

    def test_user_with_whitelist_sees_only_allowed(self, tmp_path):
        import pytest
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from orchid.auth.types import User

        p1 = tmp_path / "allowed"
        p1.mkdir()
        p2 = tmp_path / "forbidden"
        p2.mkdir()

        user = User(
            user_id="carol",
            username="carol",
            role="user",
            is_active=True,
            projects=["allowed"],  # only this one
        )
        client = self._make_app(tmp_path, [p1, p2], user)
        r = client.get("/api/projects")
        assert r.status_code == 200
        ids = {p["id"] for p in r.json()}
        assert "allowed" in ids
        assert "forbidden" not in ids


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/projects — ownership + user-namespace path
# ══════════════════════════════════════════════════════════════════════════════

class TestCreateProjectOwnership:

    def test_user_project_uses_user_namespace_path(self, tmp_path):
        import pytest
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        import orchid.auth.middleware as mw
        from orchid.auth.types import User
        from orchid.project_creator import ProjectCreator
        from orchid.projects.registry import ProjectRegistry, reset_registry, user_project_base
        from orchid.interfaces.web_server import create_app

        reset_registry()
        reg = ProjectRegistry(registry_path=tmp_path / "registry.json")

        user = User(user_id="dave", username="dave", role="user", is_active=True)

        async def _fake_optional():
            return user

        expected_base = user_project_base("dave")
        created_dir = expected_base / "myapp"
        created_dir.mkdir(parents=True, exist_ok=True)

        with patch.object(mw, "get_optional_user", _fake_optional), \
             patch("orchid.interfaces.web_server.get_optional_user", _fake_optional), \
             patch("orchid.projects.registry.get_registry", return_value=reg), \
             patch.object(ProjectCreator, "create", return_value=created_dir) as mock_create, \
             patch("orchid.config.get", side_effect=lambda k, d=None: d):
            app = create_app(project_paths=[], enable_telegram=False, enable_slack=False)
            client = TestClient(app, raise_server_exceptions=True)
            r = client.post("/api/projects", json={"name": "myapp", "description": "test"})

        assert r.status_code == 201
        # base_dir passed to creator.create() should be the user namespace
        call_kwargs = mock_create.call_args[1]
        passed_base = call_kwargs.get("base_dir")
        assert passed_base is not None
        assert str(passed_base).endswith(f"/.config/orchid/projects/dave")

    def test_user_project_registers_ownership(self, tmp_path):
        import pytest
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        import orchid.auth.middleware as mw
        from orchid.auth.types import User
        from orchid.project_creator import ProjectCreator
        from orchid.projects.registry import ProjectRegistry, reset_registry
        from orchid.interfaces.web_server import create_app

        reset_registry()
        reg = ProjectRegistry(registry_path=tmp_path / "registry.json")

        user = User(user_id="eve", username="eve", role="user", is_active=True)

        async def _fake_optional():
            return user

        created_dir = tmp_path / "proj"
        created_dir.mkdir()

        with patch.object(mw, "get_optional_user", _fake_optional), \
             patch("orchid.interfaces.web_server.get_optional_user", _fake_optional), \
             patch("orchid.projects.registry.get_registry", return_value=reg), \
             patch.object(ProjectCreator, "create", return_value=created_dir), \
             patch("orchid.config.get", side_effect=lambda k, d=None: d):
            app = create_app(project_paths=[], enable_telegram=False, enable_slack=False)
            client = TestClient(app, raise_server_exceptions=True)
            r = client.post("/api/projects", json={"name": "proj", "description": "test"})

        assert r.status_code == 201
        data = r.json()
        assert data.get("owner_id") == "eve"

        # Registry should have the entry
        entry = reg.get(data["project_id"])
        assert entry is not None
        assert entry.owner_id == "eve"

    def test_admin_project_uses_machine_profile_path(self, tmp_path):
        import pytest
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        import orchid.auth.middleware as mw
        from orchid.auth.types import User
        from orchid.project_creator import ProjectCreator
        from orchid.projects.registry import ProjectRegistry, reset_registry
        from orchid.interfaces.web_server import create_app

        reset_registry()
        reg = ProjectRegistry(registry_path=tmp_path / "registry.json")

        admin = User(user_id="admin", username="admin", role="admin", is_active=True)

        async def _fake_optional():
            return admin

        created_dir = tmp_path / "adminproj"
        created_dir.mkdir()

        with patch.object(mw, "get_optional_user", _fake_optional), \
             patch("orchid.interfaces.web_server.get_optional_user", _fake_optional), \
             patch("orchid.projects.registry.get_registry", return_value=reg), \
             patch.object(ProjectCreator, "create", return_value=created_dir) as mock_create, \
             patch("orchid.config.get", side_effect=lambda k, d=None: d):
            app = create_app(project_paths=[], enable_telegram=False, enable_slack=False)
            client = TestClient(app, raise_server_exceptions=True)
            r = client.post("/api/projects", json={"name": "adminproj", "description": "test"})

        assert r.status_code == 201
        # Admin: base_dir should be None (machine profile default)
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs.get("base_dir") is None

    def test_explicit_base_dir_overrides_user_namespace(self, tmp_path):
        import pytest
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        import orchid.auth.middleware as mw
        from orchid.auth.types import User
        from orchid.project_creator import ProjectCreator
        from orchid.projects.registry import ProjectRegistry, reset_registry
        from orchid.interfaces.web_server import create_app

        reset_registry()
        reg = ProjectRegistry(registry_path=tmp_path / "registry.json")

        user = User(user_id="frank", username="frank", role="user", is_active=True)

        async def _fake_optional():
            return user

        explicit_base = tmp_path / "custom"
        explicit_base.mkdir()
        created_dir = explicit_base / "myproj"
        created_dir.mkdir()

        with patch.object(mw, "get_optional_user", _fake_optional), \
             patch("orchid.interfaces.web_server.get_optional_user", _fake_optional), \
             patch("orchid.projects.registry.get_registry", return_value=reg), \
             patch.object(ProjectCreator, "create", return_value=created_dir) as mock_create, \
             patch("orchid.config.get", side_effect=lambda k, d=None: d):
            app = create_app(project_paths=[], enable_telegram=False, enable_slack=False)
            client = TestClient(app, raise_server_exceptions=True)
            r = client.post("/api/projects", json={
                "name": "myproj",
                "description": "test",
                "base_dir": str(explicit_base),
            })

        assert r.status_code == 201
        call_kwargs = mock_create.call_args[1]
        # explicit base_dir honoured, not the user namespace
        assert call_kwargs.get("base_dir") == explicit_base


# ══════════════════════════════════════════════════════════════════════════════
# _unregister_project propagation
# ══════════════════════════════════════════════════════════════════════════════

def test_unregister_project_removes_from_registry():
    """_unregister_project() calls registry.unregister() for the removed pid."""
    from orchid.projects.registry import reset_registry
    reset_registry()

    mock_reg = MagicMock()
    mock_reg.unregister.return_value = True

    from orchid.interfaces import web_server as _ws
    import orchid.interfaces.web_server as ws_mod

    # Inject a fake project
    original_projects = dict(ws_mod._projects)
    original_runners = dict(ws_mod._runners)
    original_managers = dict(ws_mod._managers)
    ws_mod._projects["ghost"] = "/fake/ghost"
    ws_mod._runners["ghost"] = MagicMock()
    ws_mod._managers["ghost"] = MagicMock()

    with patch("orchid.projects.registry.get_registry", return_value=mock_reg):
        pid = ws_mod._unregister_project("/fake/ghost")

    ws_mod._projects.update(original_projects)
    ws_mod._projects.pop("ghost", None)
    ws_mod._runners.pop("ghost", None)
    ws_mod._managers.pop("ghost", None)

    assert pid == "ghost"
    mock_reg.unregister.assert_called_once_with("ghost")
