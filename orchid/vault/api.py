# Orchid Vault — credential vault + notification config REST routes
#
# Registered via register_routes(app) in web_server.py.
# No `from __future__ import annotations` at module level (breaks FastAPI).

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_routes(app: Any) -> None:
    """Install /api/user/credentials and /api/user/config/notifications endpoints.

    All imports are local so that importing this module never requires FastAPI
    or cryptography at import time.  If any import fails a warning is logged
    and no routes are registered — never raises.
    """
    try:
        from fastapi import Depends, HTTPException, Request
        from orchid.auth.middleware import require_auth
        from orchid.auth.audit import AuditAction, AuditStore, make_event
        from orchid.auth.store import get_store
        from orchid.vault.store import get_vault
    except ImportError as exc:
        logger.warning("Vault API routes skipped: %s", exc)
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

    # ── Credentials ───────────────────────────────────────────────────────────

    async def list_credentials(current_user=Depends(require_auth())):
        vault = get_vault()
        try:
            keys = vault.list_keys(current_user.user_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return {"keys": keys}

    app.add_api_route("/api/user/credentials", list_credentials, methods=["GET"])

    async def set_credential(key: str, request: Request, current_user=Depends(require_auth())):
        body = await request.json()
        value = (body.get("value") or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="'value' is required")
        # Key name validation: alphanumeric, underscores, hyphens, dots only
        import re
        if not re.match(r'^[\w\-\.]+$', key):
            raise HTTPException(
                status_code=400,
                detail="Credential key may only contain letters, digits, underscores, hyphens, and dots",
            )
        vault = get_vault()
        try:
            vault.set(current_user.user_id, key, value)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        _log(current_user, AuditAction.CREDENTIAL_UPDATED, key, "success", request)
        return {"key": key, "set": True}

    app.add_api_route("/api/user/credentials/{key}", set_credential, methods=["PUT"])

    async def delete_credential(key: str, request: Request, current_user=Depends(require_auth())):
        vault = get_vault()
        try:
            deleted = vault.delete(current_user.user_id, key)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Credential '{key}' not found")
        _log(current_user, AuditAction.CREDENTIAL_DELETED, key, "success", request)
        return {"key": key, "deleted": True}

    app.add_api_route("/api/user/credentials/{key}", delete_credential, methods=["DELETE"])

    # ── Notification config ───────────────────────────────────────────────────

    _NOTIF_KEYS = {
        "email_enabled", "email_address",
        "telegram_enabled", "telegram_chat_id",
        "slack_enabled", "slack_user_id",
        "notify_on_success", "notify_on_failure",
    }

    async def get_notification_config(current_user=Depends(require_auth())):
        return current_user.notification_config or {}

    app.add_api_route(
        "/api/user/config/notifications",
        get_notification_config,
        methods=["GET"],
    )

    async def set_notification_config(
        request: Request, current_user=Depends(require_auth())
    ):
        body = await request.json()
        unknown = set(body.keys()) - _NOTIF_KEYS
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown notification config keys: {sorted(unknown)}",
            )
        # Merge over existing config (don't wipe keys not present in body)
        cfg = dict(current_user.notification_config or {})
        cfg.update({k: body[k] for k in _NOTIF_KEYS if k in body})
        current_user.notification_config = cfg
        store = get_store()
        store.update_user(current_user)
        _log(current_user, AuditAction.NOTIFICATION_CONFIG_UPDATED,
             current_user.user_id, "success", request)
        return cfg

    app.add_api_route(
        "/api/user/config/notifications",
        set_notification_config,
        methods=["PUT"],
    )
