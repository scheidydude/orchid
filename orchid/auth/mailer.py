"""Simple SMTP mailer for Orchid auth flows (invites) and task notifications.

Uses the same environment variables as the orchid-mcp-smtp server:
    SMTP_HOST       (default: smtp.gmail.com)
    SMTP_PORT       (default: 587)
    SMTP_USER       (required for sending)
    SMTP_PASSWORD   (required for sending)
    SMTP_FROM       (default: SMTP_USER)
    SMTP_USE_SSL    "true" for implicit TLS/465 (default: false = STARTTLS)

If SMTP_USER / SMTP_PASSWORD are not set, ``is_configured()`` returns False
and all send functions return False without raising.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """Return True if SMTP credentials are present in the environment."""
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


# ── Public send helpers ───────────────────────────────────────────────────────

def send_invite(to_email: str, invite_url: str, invited_by: str, base_url: str = "") -> bool:
    """Send an invite email to *to_email*.

    Returns True if the email was dispatched, False if SMTP is not configured
    or sending failed (error is logged, never raised).
    """
    if not is_configured():
        return False

    subject = "You've been invited to Orchid"
    text = (
        f"Hi,\n\n"
        f"{invited_by} has invited you to Orchid, an AI agent orchestration platform.\n\n"
        f"Click the link below to set your password and activate your account:\n\n"
        f"  {invite_url}\n\n"
        f"This link expires in 48 hours.\n\n"
        f"If you didn't expect this invitation, you can safely ignore this email.\n"
    )
    html = (
        f"<p>Hi,</p>"
        f"<p><strong>{invited_by}</strong> has invited you to <strong>Orchid</strong>, "
        f"an AI agent orchestration platform.</p>"
        f"<p><a href='{invite_url}' style='display:inline-block;padding:10px 20px;"
        f"background:#7c3aed;color:#fff;border-radius:6px;text-decoration:none;'>"
        f"Accept invitation</a></p>"
        f"<p>Or copy this link: <code>{invite_url}</code></p>"
        f"<p><em>This link expires in 48 hours.</em></p>"
    )
    return _send(to_email, subject, text, html)


def send_task_notification(
    to_email: str,
    task_name: str,
    status: str,
    run_id: str,
    output: str = "",
) -> bool:
    """Send a task run notification email.

    Returns True if sent, False otherwise (non-raising).
    """
    if not is_configured():
        return False

    icon = "✓" if status == "success" else "✗"
    subject = f"{icon} Orchid task '{task_name}' — {status}"
    snippet = (output[:500] + "…") if len(output) > 500 else output
    text = f"Task: {task_name}\nStatus: {status}\nRun ID: {run_id}\n\n{snippet}"
    html = (
        f"<p><strong>Task:</strong> {task_name}</p>"
        f"<p><strong>Status:</strong> {status}</p>"
        f"<p><strong>Run ID:</strong> <code>{run_id}</code></p>"
        + (f"<pre style='background:#1a1a2e;color:#cdd6f4;padding:12px;border-radius:6px;"
           f"overflow-x:auto'>{snippet}</pre>" if snippet else "")
    )
    return _send(to_email, subject, text, html)


# ── Internal ──────────────────────────────────────────────────────────────────

def _send(to: str, subject: str, body_text: str, body_html: str | None = None) -> bool:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    from_addr = os.environ.get("SMTP_FROM", user)
    use_ssl = os.environ.get("SMTP_USE_SSL", "").lower() in ("true", "1", "yes")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(body_text, "plain"))
    if body_html:
        msg.attach(MIMEText(body_html, "html"))

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port) as smtp:
                smtp.login(user, password)
                smtp.sendmail(from_addr, [to], msg.as_string())
        else:
            with smtplib.SMTP(host, port) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.sendmail(from_addr, [to], msg.as_string())
        logger.debug("Email sent to %s: %s", to, subject)
        return True
    except Exception as exc:
        logger.error("SMTP send failed (to=%s): %s", to, exc)
        return False
