#!/usr/bin/env python3
"""Orchid built-in SMTP MCP server.

Implements the MCP protocol (JSON-RPC 2.0 over stdio) without any external
MCP SDK.  Exposes a single ``send_email`` tool backed by :mod:`smtplib`.

Configuration via environment variables
---------------------------------------
SMTP_HOST       SMTP server hostname         (default: smtp.gmail.com)
SMTP_PORT       SMTP port                    (default: 587)
SMTP_USER       Login username / sender      (required)
SMTP_PASSWORD   Login password or app token  (required)
SMTP_FROM       From display string override (default: SMTP_USER)
SMTP_USE_SSL    "true" for implicit SSL/465  (default: false = STARTTLS)

Usage
-----
Run directly::

    orchid-mcp-smtp

Or reference in config.yaml::

    mcp_servers:
      smtp:
        transport: stdio
        command: ["orchid-mcp-smtp"]
        env:
          SMTP_USER: "${SMTP_USER}"
          SMTP_PASSWORD: "${SMTP_PASSWORD}"
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

# Log to stderr only — stdout is the JSON-RPC transport.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
logger = logging.getLogger("orchid.servers.smtp")

# ── MCP protocol constants ────────────────────────────────────────────────────

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "orchid-smtp"
_SERVER_VERSION = "1.0.0"

_TOOL_SCHEMA: dict[str, Any] = {
    "name": "send_email",
    "description": (
        "Send an email via SMTP. "
        "Supports plain-text and optional HTML body (multipart/alternative). "
        "Sender address comes from SMTP_USER env var unless overridden."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient address(es), comma-separated.",
            },
            "subject": {
                "type": "string",
                "description": "Email subject line.",
            },
            "body": {
                "type": "string",
                "description": "Plain-text email body.",
            },
            "html_body": {
                "type": "string",
                "description": (
                    "Optional HTML body. "
                    "When provided the message is sent as multipart/alternative."
                ),
            },
            "cc": {
                "type": "string",
                "description": "CC recipients, comma-separated (optional).",
            },
            "bcc": {
                "type": "string",
                "description": "BCC recipients, comma-separated (optional).",
            },
            "from_addr": {
                "type": "string",
                "description": (
                    "From address override. "
                    "Defaults to SMTP_FROM env var, then SMTP_USER."
                ),
            },
        },
        "required": ["to", "subject", "body"],
    },
}


# ── SMTP helpers ──────────────────────────────────────────────────────────────


def _addr_list(value: str) -> list[str]:
    """Split a comma-separated address string into a list, stripping blanks."""
    return [a.strip() for a in value.split(",") if a.strip()]


def _build_message(
    from_addr: str,
    to: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    cc: str | None = None,
) -> MIMEMultipart | MIMEText:
    """Build a MIME message from the given fields.

    Returns a ``MIMEMultipart`` when *html_body* is provided, otherwise a
    plain ``MIMEText``.
    """
    if html_body:
        msg: MIMEMultipart | MIMEText = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")

    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    return msg


def _send_via_smtp(
    host: str,
    port: int,
    user: str,
    password: str,
    use_ssl: bool,
    from_addr: str,
    recipients: list[str],
    msg: MIMEMultipart | MIMEText,
) -> None:
    """Open an SMTP connection, authenticate, send, and close."""
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.sendmail(from_addr, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(user, password)
            smtp.sendmail(from_addr, recipients, msg.as_string())


# ── MCP server ────────────────────────────────────────────────────────────────


class SMTPMCPServer:
    """Stateless MCP server that exposes the ``send_email`` tool.

    All configuration is read from environment variables on each
    ``send_email`` call so that per-user credentials injected via
    ``server_credentials`` in agent_tool task configs take effect
    without restarting the server.
    """

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Route a JSON-RPC request to the appropriate handler.

        Args:
            msg: Decoded JSON-RPC request dict (must have ``id`` field).

        Returns:
            A JSON-RPC response dict ready for serialisation.
        """
        msg_id = msg.get("id")
        method = msg.get("method", "")

        try:
            if method == "initialize":
                result = self._handle_initialize(msg.get("params", {}))
            elif method == "tools/list":
                result = self._handle_list_tools()
            elif method == "tools/call":
                result = self._handle_call_tool(msg.get("params", {}))
            else:
                return self._error(msg_id, -32601, f"Method not found: {method!r}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unhandled error in dispatch for method %r", method)
            return self._error(msg_id, -32603, f"Internal error: {exc}")

        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
        }

    def _handle_list_tools(self) -> dict[str, Any]:
        return {"tools": [_TOOL_SCHEMA]}

    def _handle_call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "")
        if name != "send_email":
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {name!r}"}],
                "isError": True,
            }
        return self._send_email(params.get("arguments", {}))

    # ------------------------------------------------------------------
    # Tool implementation
    # ------------------------------------------------------------------

    def _send_email(self, args: dict[str, Any]) -> dict[str, Any]:
        """Validate arguments, build MIME message, send via SMTP.

        Returns a JSON-RPC tool result dict.
        """
        # --- Validate required args ----------------------------------
        missing = [f for f in ("to", "subject", "body") if not args.get(f, "").strip()]
        if missing:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Missing required field(s): {', '.join(missing)}",
                    }
                ],
                "isError": True,
            }

        # --- Read config from env ------------------------------------
        host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        port = int(os.environ.get("SMTP_PORT", "587"))
        user = os.environ.get("SMTP_USER", "")
        password = os.environ.get("SMTP_PASSWORD", "")
        use_ssl = os.environ.get("SMTP_USE_SSL", "false").lower() == "true"

        if not user or not password:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "SMTP_USER and SMTP_PASSWORD env vars are required.",
                    }
                ],
                "isError": True,
            }

        from_addr = args.get("from_addr", "").strip() or os.environ.get("SMTP_FROM", user)
        to_str: str = args["to"]
        cc_str: str | None = args.get("cc", "").strip() or None
        bcc_str: str | None = args.get("bcc", "").strip() or None

        recipients = _addr_list(to_str)
        if cc_str:
            recipients += _addr_list(cc_str)
        if bcc_str:
            recipients += _addr_list(bcc_str)

        # --- Build and send ------------------------------------------
        msg = _build_message(
            from_addr=from_addr,
            to=to_str,
            subject=args["subject"],
            body=args["body"],
            html_body=args.get("html_body") or None,
            cc=cc_str,
        )

        try:
            _send_via_smtp(
                host=host,
                port=port,
                user=user,
                password=password,
                use_ssl=use_ssl,
                from_addr=from_addr,
                recipients=recipients,
                msg=msg,
            )
        except smtplib.SMTPAuthenticationError as exc:
            logger.error("SMTP auth failure: %s", exc)
            return {
                "content": [{"type": "text", "text": f"SMTP authentication failed: {exc}"}],
                "isError": True,
            }
        except smtplib.SMTPException as exc:
            logger.error("SMTP error: %s", exc)
            return {
                "content": [{"type": "text", "text": f"SMTP error: {exc}"}],
                "isError": True,
            }
        except OSError as exc:
            logger.error("Network error: %s", exc)
            return {
                "content": [{"type": "text", "text": f"Network error: {exc}"}],
                "isError": True,
            }

        summary = f"Email sent to {to_str}"
        if cc_str:
            summary += f", cc: {cc_str}"
        logger.info(summary)
        return {"content": [{"type": "text", "text": summary}], "isError": False}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Run the SMTP MCP server, reading JSON-RPC from stdin, writing to stdout.

    Notifications (messages without an ``id``) are silently consumed with
    no response, matching the MCP spec.
    """
    server = SMTPMCPServer()
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            # Can't send a proper error without an id; log and continue.
            logger.error("JSON decode error: %s — input: %.120s", exc, raw)
            continue

        # Notifications have no "id" — consume silently, no response.
        if "id" not in msg:
            continue

        response = server.dispatch(msg)
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
