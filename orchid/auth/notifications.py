"""Task run notification dispatcher for Orchid (Phase 2).

Called by ``CronEngine._run_task`` after each execution. Reads the owner's
``notification_config`` from the auth store and dispatches notifications to
configured channels.

Channel support:
    email     — via orchid.auth.mailer (SMTP env vars required)
    telegram  — via CentralTelegramBot.send_dm() when bot manager is running
    slack     — via CentralSlackBot.send_dm() when bot manager is running

Configuration keys in ``User.notification_config``:
    email_enabled:       bool  (default False)
    email_address:       str   (defaults to User.email)
    telegram_enabled:    bool  (default False)
    telegram_chat_id:    str
    slack_enabled:       bool  (default False)
    slack_user_id:       str
    notify_on_success:   bool  (default False)  — global override
    notify_on_failure:   bool  (default True)   — global override

Per-task ``notify_on_success`` / ``notify_on_failure`` on ``ScheduledTask``
take precedence over the global config.  If both are False, no notification
is sent regardless of channel settings.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_STATUS_EMOJI = {
    "success": "✅",
    "failure": "❌",
    "timeout": "⏱️",
}
_OUTPUT_MAX = 500


def _format_dm_text(task_name: str, status: str, run_id: str, output: str) -> str:
    """Format a short DM message for Telegram or Slack."""
    emoji = _STATUS_EMOJI.get(status, "🔔")
    lines = [
        "🌸 *Orchid Task Notification*",
        f"Task: {task_name}",
        f"Status: {emoji} {status}",
        f"Run: `{run_id}`",
    ]
    if output:
        snippet = output[:_OUTPUT_MAX]
        if len(output) > _OUTPUT_MAX:
            snippet += "…"
        lines.append(f"Output:\n```\n{snippet}\n```")
    return "\n".join(lines)


def dispatch_task_notification(owner_id: str, task_dict: dict, run: object) -> None:
    """Fire-and-forget notification after a task run.

    Designed to be called from within a background thread — never raises.

    Args:
        owner_id:  The user_id who owns the task.
        task_dict: The raw scheduled-task dict (has notify_on_* keys).
        run:       A ``TaskRun`` instance with .status, .run_id, .output.
    """
    try:
        _dispatch(owner_id, task_dict, run)
    except Exception:
        logger.exception("Notification dispatch error for owner=%s run=%s", owner_id,
                         getattr(run, "run_id", "?"))


def _dispatch(owner_id: str, task_dict: dict, run: object) -> None:
    status = getattr(run, "status", "unknown")
    run_id = getattr(run, "run_id", "unknown")
    output = getattr(run, "output", "") or ""
    task_name = task_dict.get("name", task_dict.get("task_id", "?"))

    # Determine whether this status warrants notification
    notify_on_success = task_dict.get("notify_on_success", False)
    notify_on_failure = task_dict.get("notify_on_failure", True)
    should_notify = (status == "success" and notify_on_success) or \
                    (status in ("failure", "timeout") and notify_on_failure)
    if not should_notify:
        return

    # Load user config
    try:
        from orchid.auth.store import get_store
        user = get_store().get_user(owner_id)
    except Exception as exc:
        logger.warning("Could not load user %s for notification: %s", owner_id, exc)
        return

    if user is None:
        return

    cfg = user.notification_config or {}

    # Per-user global overrides (if set, they gate channels)
    global_on_success = cfg.get("notify_on_success", True)
    global_on_failure = cfg.get("notify_on_failure", True)
    if status == "success" and not global_on_success:
        return
    if status in ("failure", "timeout") and not global_on_failure:
        return

    # ── Email ─────────────────────────────────────────────────────────────────
    if cfg.get("email_enabled", False):
        email_addr = cfg.get("email_address") or user.email
        if email_addr:
            from orchid.auth.mailer import send_task_notification
            sent = send_task_notification(email_addr, task_name, status, run_id, output)
            logger.debug("Email notification for run %s: sent=%s", run_id, sent)
        else:
            logger.debug("Email notification skipped — no address for user %s", owner_id)

    # ── Telegram ──────────────────────────────────────────────────────────────
    if cfg.get("telegram_enabled", False) and cfg.get("telegram_chat_id"):
        chat_id = cfg["telegram_chat_id"]
        try:
            from orchid.interfaces.central_bot import get_bot_manager
            mgr = get_bot_manager()
            if mgr is not None:
                mgr.send_telegram_dm(int(chat_id), _format_dm_text(task_name, status, run_id, output))
                logger.debug("Telegram DM dispatched: chat_id=%s run=%s", chat_id, run_id)
            else:
                logger.info(
                    "Telegram DM skipped — bot manager not running "
                    "(chat_id=%s task=%s status=%s run=%s)",
                    chat_id, task_name, status, run_id,
                )
        except Exception as exc:
            logger.warning("Telegram notification failed (chat_id=%s run=%s): %s", chat_id, run_id, exc)

    # ── Slack ─────────────────────────────────────────────────────────────────
    if cfg.get("slack_enabled", False) and cfg.get("slack_user_id"):
        slack_uid = cfg["slack_user_id"]
        try:
            from orchid.interfaces.central_bot import get_bot_manager
            mgr = get_bot_manager()
            if mgr is not None:
                mgr.send_slack_dm(slack_uid, _format_dm_text(task_name, status, run_id, output))
                logger.debug("Slack DM dispatched: user_id=%s run=%s", slack_uid, run_id)
            else:
                logger.info(
                    "Slack DM skipped — bot manager not running "
                    "(user_id=%s task=%s status=%s run=%s)",
                    slack_uid, task_name, status, run_id,
                )
        except Exception as exc:
            logger.warning("Slack notification failed (user_id=%s run=%s): %s", slack_uid, run_id, exc)
