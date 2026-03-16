"""Telegram-safe output formatters — plain text only, no markdown.

Rules:
- No rich markup, no box-drawing characters, no markdown syntax
- Plain emoji for status indicators
- Truncate to max_message_length (default 4000 chars, Telegram limit)
- parse_mode=None is assumed; callers must not set parse_mode on these outputs
"""

from __future__ import annotations

from typing import Any

_MAX_LEN = 4000

_STATUS_EMOJI = {
    "TODO": "⬜",
    "IN_PROGRESS": "🔄",
    "DONE": "✅",
    "BLOCKED": "🔴",
    "CANCELLED": "⬛",
}

_TYPE_ABBREV = {
    "draft": "dft",
    "code_generate": "code",
    "orchestrate": "orch",
    "review": "rev",
    "plan": "plan",
    "critique": "crit",
    "synthesize": "syn",
    "search": "srch",
    "summarize": "sum",
    "transform": "xfrm",
}


def _truncate(text: str, limit: int = _MAX_LEN, suffix: str = "\n…(truncated)") -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)] + suffix


def format_status(session: Any) -> str:
    """Format project status for Telegram — plain text, no markdown."""
    lines: list[str] = []
    lines.append(f"📋 {session.project_name}")
    if session.project_description:
        lines.append(session.project_description)
    lines.append("")

    if not session.tasks:
        lines.append("No tasks found.")
    else:
        completed_ids = {t.id for t in session.tasks if t.status.value == "DONE"}
        counts: dict[str, int] = {}
        for t in session.tasks:
            sv = t.status.value
            counts[sv] = counts.get(sv, 0) + 1

        lines.append("Tasks:")
        for t in session.tasks:
            emoji = _STATUS_EMOJI.get(t.status.value, "❓")
            ttype = _TYPE_ABBREV.get(t.type, t.type[:4])
            line = f"{emoji} {t.id}  {t.title}  ({ttype} p{t.priority})"
            # Show blocked deps
            if t.depends_on and not t.is_runnable(completed_ids):
                waiting = ", ".join(t.depends_on)
                line += f"  [waiting on: {waiting}]"
            lines.append(line)

        summary_parts = [f"{v}x{k}" for k, v in counts.items() if v]
        lines.append("")
        lines.append("  ".join(summary_parts))

    if session.hot_memory:
        lines.append("")
        lines.append("Hot memory:")
        lines.append(session.hot_memory[:500].strip())

    return _truncate("\n".join(lines))


def format_task_list(tasks: list[Any]) -> str:
    """Format a flat task list for Telegram."""
    if not tasks:
        return "No tasks."
    lines: list[str] = []
    for t in tasks:
        emoji = _STATUS_EMOJI.get(t.status.value, "❓")
        lines.append(f"{emoji} {t.id}  {t.title}")
    return _truncate("\n".join(lines))


def format_recall_results(results: list[dict[str, Any]]) -> str:
    """Format vector recall results for Telegram."""
    if not results:
        return "No results found."
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {})
        rtype = meta.get("type", "note")
        score = 1 - r.get("distance", 1.0)
        ts = meta.get("timestamp", "")[:16].replace("T", " ")
        header = f"[{i}] type={rtype}  score={score:.2f}"
        if ts:
            header += f"  {ts}"
        lines.append(header)
        lines.append(r.get("text", "")[:300].strip())
        lines.append("")
    return _truncate("\n".join(lines))


def format_search_results(results: list[dict[str, Any]]) -> str:
    """Format web search results for Telegram."""
    if not results:
        return "No results."
    if len(results) == 1 and results[0].get("title") in ("error", ""):
        return f"⚠️ Search error: {results[0].get('snippet', 'unknown error')}"
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        lines.append(f"[{i}] {title}")
        if url:
            lines.append(url)
        if snippet:
            lines.append(snippet[:300])
        lines.append("")
    return _truncate("\n".join(lines))


def format_task_complete(task_id: str, result: str) -> str:
    """Format a task-complete notification."""
    preview = result[:200].strip() if result else "(no output)"
    return _truncate(f"✅ {task_id} done\n\n{preview}")


def format_task_failed(task_id: str, error: str) -> str:
    """Format a task-failed notification."""
    preview = str(error)[:200].strip()
    return _truncate(f"❌ {task_id} failed\n\n{preview}")


def format_task_started(task_id: str, title: str) -> str:
    return f"🤖 Starting {task_id}: {title}…"


def format_auto_summary(done: list[str], failed: list[str]) -> str:
    lines = ["Auto run complete.", ""]
    if done:
        lines.append(f"✅ Done: {', '.join(done)}")
    if failed:
        lines.append(f"❌ Failed: {', '.join(failed)}")
    if not done and not failed:
        lines.append("No tasks were run.")
    return "\n".join(lines)


# ── Notification event formatters ─────────────────────────────────────────────

def format_notification(event: str, data: dict[str, Any]) -> str | None:
    """Format a lifecycle notification event into a Telegram message.

    Returns None if the event should not be displayed.
    """
    if event == "session_start":
        project = data.get("project", "project")
        pending = data.get("pending", 0)
        return f"🌸 Orchid session started — {pending} task{'s' if pending != 1 else ''} pending ({project})"

    if event == "task_start":
        task_id = data.get("task_id", "?")
        title = data.get("title", "")
        remaining = data.get("remaining")
        msg = f"🤖 {task_id} starting — {title}"
        if remaining is not None:
            msg += f"  ({remaining} remaining)"
        return msg

    if event == "task_progress":
        task_id = data.get("task_id", "?")
        iteration = data.get("iter", "?")
        snippet = data.get("thought_snippet", "")
        msg = f"⚙️ {task_id} iter {iteration}"
        if snippet:
            msg += f" — {snippet[:80]}"
        return msg

    if event == "task_complete":
        task_id = data.get("task_id", "?")
        snippet = data.get("result_snippet", "")
        done_so_far = data.get("done_so_far")
        msg = f"✅ {task_id} done"
        if done_so_far is not None:
            msg += f"  ({done_so_far} completed)"
        if snippet:
            msg += f"\n{snippet[:200]}"
        return _truncate(msg)

    if event == "task_failed":
        task_id = data.get("task_id", "?")
        error = data.get("error", "unknown error")
        return _truncate(f"❌ {task_id} failed\n{str(error)[:200]}")

    if event == "task_blocked":
        task_id = data.get("task_id", "?")
        waiting_on = data.get("waiting_on", [])
        msg = f"⚠️ {task_id} blocked"
        if waiting_on:
            msg += f" — waiting on: {', '.join(waiting_on)}"
        return msg

    if event == "needs_input":
        task_id = data.get("task_id", "?")
        return f"❓ {task_id} needs input — reply /inject <context>"

    if event == "session_complete":
        done = data.get("done", [])
        failed = data.get("failed", [])
        total = len(done) + len(failed)
        msg = f"🎉 Session complete — {len(done)}/{total} tasks done"
        if failed:
            msg += f"  ({len(failed)} failed: {', '.join(failed)})"
        return msg

    if event == "session_idle":
        return "💤 No tasks to run — queue empty"

    if event == "provider_unavailable":
        provider = data.get("provider", "unknown")
        missing = data.get("missing", "")
        fix = data.get("fix", "")
        msg = f"🔌 Provider '{provider}' unavailable — {missing}"
        if fix:
            msg += f"\nFix: {fix}"
        return _truncate(msg)

    return None
