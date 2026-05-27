# Orchid MCP Catalog — admin + user REST routes (Phase 3)
#
# Registered via:
#   register_admin_routes(app)  →  /api/admin/mcp/*  (admin-only)
#   register_user_routes(app)   →  /api/user/mcp/*   (any auth'd user)
#
# No `from __future__ import annotations` at module level (breaks FastAPI).

import logging
from dataclasses import asdict
from typing import Any

logger = logging.getLogger(__name__)


# ── Admin routes ──────────────────────────────────────────────────────────────

def register_admin_routes(app: Any) -> None:
    """Install /api/admin/mcp/* endpoints (admin-only).

    All imports are local so this module never hard-requires FastAPI at
    import time.  Logs a warning and skips silently if any import fails.
    """
    try:
        from fastapi import Depends, HTTPException, Request
        from orchid.auth.middleware import require_auth
        from orchid.auth.audit import AuditAction, AuditStore, make_event
        from orchid.mcp.catalog import MCPServerEntry, get_catalog
    except ImportError as exc:
        logger.warning("MCP admin routes skipped: %s", exc)
        return

    _audit = AuditStore()

    def _log(user, action: str, resource: str, result: str, request=None, detail: str = "") -> None:
        try:
            ip = request.client.host if request and request.client else ""
            _audit.log(make_event(
                user_id=user.user_id, action=action, resource=resource,
                result=result, ip=ip, detail=detail,
            ))
        except Exception:
            pass

    # GET /api/admin/mcp/catalog
    async def list_catalog(current_user=Depends(require_auth(role="admin"))):
        cat = get_catalog()
        return {"servers": [asdict(e) for e in cat.list_servers()]}

    app.add_api_route("/api/admin/mcp/catalog", list_catalog, methods=["GET"])

    # POST /api/admin/mcp/catalog
    async def create_catalog_entry(
        request: Request,
        current_user=Depends(require_auth(role="admin")),
    ):
        body = await request.json()
        server_id = (body.get("server_id") or "").strip()
        name = (body.get("name") or "").strip()
        transport = (body.get("transport") or "stdio").strip()
        config = body.get("config") or {}

        if not server_id:
            raise HTTPException(400, "server_id required")
        if not name:
            raise HTTPException(400, "name required")
        if transport not in ("stdio", "http"):
            raise HTTPException(400, "transport must be 'stdio' or 'http'")
        if transport == "stdio" and not config.get("command"):
            raise HTTPException(400, "config.command required for stdio transport")
        if transport == "http" and not config.get("url"):
            raise HTTPException(400, "config.url required for http transport")

        scope = body.get("scope", "shared")
        if scope not in ("shared", "private", "admin-only"):
            raise HTTPException(400, "scope must be 'shared', 'private', or 'admin-only'")

        entry = MCPServerEntry(
            server_id=server_id,
            name=name,
            transport=transport,
            config=config,
            scope=scope,
            allowed_roles=list(body.get("allowed_roles") or []),
            allowed_users=list(body.get("allowed_users") or []),
            requires_credential=body.get("requires_credential") or None,
        )
        cat = get_catalog()
        try:
            cat.add_server(entry)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        _log(current_user, AuditAction.MCP_SERVER_CREATED, server_id, "success", request)
        return asdict(entry)

    app.add_api_route("/api/admin/mcp/catalog", create_catalog_entry, methods=["POST"])

    # GET /api/admin/mcp/catalog/{server_id}
    async def get_catalog_entry(
        server_id: str,
        current_user=Depends(require_auth(role="admin")),
    ):
        cat = get_catalog()
        entry = cat.get_server(server_id)
        if entry is None:
            raise HTTPException(404, f"Server '{server_id}' not found")
        return asdict(entry)

    app.add_api_route(
        "/api/admin/mcp/catalog/{server_id}", get_catalog_entry, methods=["GET"]
    )

    # PUT /api/admin/mcp/catalog/{server_id}
    async def update_catalog_entry(
        server_id: str,
        request: Request,
        current_user=Depends(require_auth(role="admin")),
    ):
        body = await request.json()
        _updatable = {
            "name", "transport", "config", "scope",
            "allowed_roles", "allowed_users", "requires_credential",
        }
        updates = {k: v for k, v in body.items() if k in _updatable}
        if not updates:
            raise HTTPException(400, "No updatable fields provided")
        cat = get_catalog()
        try:
            entry = cat.update_server(server_id, **updates)
        except KeyError:
            raise HTTPException(404, f"Server '{server_id}' not found")
        _log(current_user, AuditAction.MCP_SERVER_UPDATED, server_id, "success", request)
        return asdict(entry)

    app.add_api_route(
        "/api/admin/mcp/catalog/{server_id}", update_catalog_entry, methods=["PUT"]
    )

    # DELETE /api/admin/mcp/catalog/{server_id}
    async def delete_catalog_entry(
        server_id: str,
        request: Request,
        current_user=Depends(require_auth(role="admin")),
    ):
        cat = get_catalog()
        deleted = cat.delete_server(server_id)
        if not deleted:
            raise HTTPException(404, f"Server '{server_id}' not found")
        _log(current_user, AuditAction.MCP_SERVER_DELETED, server_id, "success", request)
        return {"server_id": server_id, "deleted": True}

    app.add_api_route(
        "/api/admin/mcp/catalog/{server_id}", delete_catalog_entry, methods=["DELETE"]
    )

    # PUT /api/admin/mcp/catalog/{server_id}/grant
    async def grant_catalog_access(
        server_id: str,
        request: Request,
        current_user=Depends(require_auth(role="admin")),
    ):
        body = await request.json()
        role = (body.get("role") or "").strip() or None
        user_id = (body.get("user_id") or "").strip() or None
        if not role and not user_id:
            raise HTTPException(400, "Provide 'role' or 'user_id'")
        cat = get_catalog()
        try:
            entry = cat.grant_access(server_id, role=role, user_id=user_id)
        except KeyError:
            raise HTTPException(404, f"Server '{server_id}' not found")
        _log(
            current_user, AuditAction.MCP_ACCESS_GRANTED, server_id, "success",
            request, detail=f"role={role} user_id={user_id}",
        )
        return asdict(entry)

    app.add_api_route(
        "/api/admin/mcp/catalog/{server_id}/grant",
        grant_catalog_access,
        methods=["PUT"],
    )

    # PUT /api/admin/mcp/catalog/{server_id}/revoke
    async def revoke_catalog_access(
        server_id: str,
        request: Request,
        current_user=Depends(require_auth(role="admin")),
    ):
        body = await request.json()
        role = (body.get("role") or "").strip() or None
        user_id = (body.get("user_id") or "").strip() or None
        if not role and not user_id:
            raise HTTPException(400, "Provide 'role' or 'user_id'")
        cat = get_catalog()
        try:
            entry = cat.revoke_access(server_id, role=role, user_id=user_id)
        except KeyError:
            raise HTTPException(404, f"Server '{server_id}' not found")
        _log(
            current_user, AuditAction.MCP_ACCESS_REVOKED, server_id, "success",
            request, detail=f"role={role} user_id={user_id}",
        )
        return asdict(entry)

    app.add_api_route(
        "/api/admin/mcp/catalog/{server_id}/revoke",
        revoke_catalog_access,
        methods=["PUT"],
    )


# ── User routes ───────────────────────────────────────────────────────────────

def register_user_routes(app: Any) -> None:
    """Install /api/user/mcp/* endpoints (any authenticated user).

    GET  /api/user/mcp/servers          list servers granted to the caller
    POST /api/user/mcp/servers          add a private server (allow_user_mcp)
    DELETE /api/user/mcp/servers/{id}   remove a private server
    """
    try:
        from fastapi import Depends, HTTPException, Request
        from orchid.auth.middleware import require_auth
        from orchid.auth.audit import AuditAction, AuditStore, make_event
        from orchid.mcp.catalog import get_catalog, UserMCPStore
        from orchid.config import get as _cfg_get
    except ImportError as exc:
        logger.warning("MCP user routes skipped: %s", exc)
        return

    _audit = AuditStore()

    def _log(user, action: str, resource: str, result: str, request=None, detail: str = "") -> None:
        try:
            ip = request.client.host if request and request.client else ""
            _audit.log(make_event(
                user_id=user.user_id, action=action, resource=resource,
                result=result, ip=ip, detail=detail,
            ))
        except Exception:
            pass

    # GET /api/user/mcp/servers
    async def list_user_servers(current_user=Depends(require_auth())):
        cat = get_catalog()
        shared = cat.get_servers_for_user(current_user.user_id, current_user.role)
        user_store = UserMCPStore()
        private = user_store.list_servers(current_user.user_id)
        return {
            "shared": [asdict(e) for e in shared],
            "private": private,
        }

    app.add_api_route("/api/user/mcp/servers", list_user_servers, methods=["GET"])

    # POST /api/user/mcp/servers
    async def add_user_server(
        request: Request,
        current_user=Depends(require_auth()),
    ):
        allow = _cfg_get("web.allow_user_mcp", True)
        if not allow:
            raise HTTPException(403, "Adding private MCP servers is disabled by admin")

        body = await request.json()
        name = (body.get("name") or "").strip()
        transport = (body.get("transport") or "stdio").strip()

        if not name:
            raise HTTPException(400, "name required")
        if transport not in ("stdio", "http"):
            raise HTTPException(400, "transport must be 'stdio' or 'http'")
        if transport == "stdio" and not (body.get("command") or body.get("config", {}).get("command")):
            raise HTTPException(400, "command required for stdio transport")
        if transport == "http" and not (body.get("url") or body.get("config", {}).get("url")):
            raise HTTPException(400, "url required for http transport")

        config = dict(body)
        config.setdefault("name", name)
        config.setdefault("transport", transport)

        user_store = UserMCPStore()
        try:
            server = user_store.add_server(current_user.user_id, config)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        _log(
            current_user, AuditAction.USER_MCP_SERVER_ADDED,
            server.get("server_id", ""), "success", request,
        )
        return server

    app.add_api_route("/api/user/mcp/servers", add_user_server, methods=["POST"])

    # DELETE /api/user/mcp/servers/{server_id}
    async def delete_user_server(
        server_id: str,
        request: Request,
        current_user=Depends(require_auth()),
    ):
        user_store = UserMCPStore()
        deleted = user_store.delete_server(current_user.user_id, server_id)
        if not deleted:
            raise HTTPException(404, f"Private server '{server_id}' not found")
        _log(current_user, AuditAction.USER_MCP_SERVER_DELETED, server_id, "success", request)
        return {"server_id": server_id, "deleted": True}

    app.add_api_route(
        "/api/user/mcp/servers/{server_id}", delete_user_server, methods=["DELETE"]
    )
