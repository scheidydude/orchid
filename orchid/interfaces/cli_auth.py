"""CLI session store and server-auth helpers (Phase 1).

Session file: ~/.config/orchid/cli_session.json (mode 0600)
Contains: user_id, username, role, access_token, refresh_token, server_url, issued_at.

Phases 2-4 only need user_id + role from the session — no token validation required.
Token refresh is only attempted for server-side calls (whoami).
"""
from __future__ import annotations

import json
import logging
import os
import stat
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CLI_SESSION_PATH = Path("~/.config/orchid/cli_session.json").expanduser()
DEFAULT_SERVER_URL = "http://localhost:7842"

_TOKEN_TTL_SECONDS = 8 * 3600   # server issues 8 h access tokens
_REFRESH_THRESHOLD = 7 * 3600   # attempt refresh when token is > 7 h old


def load_cli_session() -> dict[str, Any] | None:
    """Return stored CLI session dict, or None if absent / unreadable."""
    if not CLI_SESSION_PATH.exists():
        return None
    try:
        return json.loads(CLI_SESSION_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_cli_session(data: dict[str, Any]) -> None:
    CLI_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLI_SESSION_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(CLI_SESSION_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0600


def clear_cli_session() -> None:
    if CLI_SESSION_PATH.exists():
        CLI_SESSION_PATH.unlink()


def _try_refresh(session: dict[str, Any]) -> dict[str, Any] | None:
    """POST /api/auth/refresh with stored refresh token. Returns updated session or None."""
    import httpx

    server_url = session.get("server_url", DEFAULT_SERVER_URL).rstrip("/")
    refresh_token = session.get("refresh_token", "")
    if not refresh_token:
        return None

    try:
        resp = httpx.post(
            f"{server_url}/api/auth/refresh",
            json={"refresh_token": refresh_token},
            timeout=10,
        )
    except Exception as exc:
        logger.debug("CLI session refresh failed (server unreachable): %s", exc)
        return None

    if resp.status_code != 200:
        logger.debug("CLI session refresh rejected: HTTP %d", resp.status_code)
        return None

    data = resp.json()
    updated: dict[str, Any] = {
        **session,
        "access_token": data.get("access_token", session["access_token"]),
        "issued_at": time.time(),
    }
    new_refresh = resp.cookies.get("orchid_refresh") or data.get("refresh_token")
    if new_refresh:
        updated["refresh_token"] = new_refresh
    save_cli_session(updated)
    return updated


def get_valid_session() -> dict[str, Any] | None:
    """Load session, refreshing if the access token is nearing expiry.

    Falls back to the stored session if refresh fails (server may be down).
    Returns None if no session file exists.
    """
    session = load_cli_session()
    if session is None:
        return None
    issued_at = float(session.get("issued_at", 0.0))
    age = time.time() - issued_at
    if age > _REFRESH_THRESHOLD:
        refreshed = _try_refresh(session)
        return refreshed if refreshed is not None else session
    return session
