"""Slack-safe output formatters using mrkdwn and Block Kit.

Rules:
- Slack mrkdwn: *bold*, _italic_, `code`, ```block```
- Block Kit sections for rich status display
- Truncate to 3000 chars (Slack block text limit)
- Thread replies for task progress (not channel spam)
"""

from __future__ import annotations

from typing import Any

_MAX_TEXT = 3000   # Block Kit section text limit
_MAX_MSG = 4000    # Plain message limit

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


def _trunc(text: str, limit: int = _MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 14] + "\n…(truncated)"


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _trunc(text)}}


def _divider() -> dict[str, Any]:
    return {"type": "divider"}


def _header(text: str) -> dict[str, Any]:
    return {"type": "header", "text": {"type": "plain_text", "text": text[:150], "emoji": True}}


def format_status(session: Any) -> list[dict[str, Any]]:
    """Format project status as Block Kit blocks."""
    blocks: list[dict[str, Any]] = [
        _header(f"📋 {session.project_name}"),
    ]

    if session.project_description:
        blocks.append(_section(f"_{session.project_description}_"))

    if not session.tasks:
        blocks.append(_section("No tasks found."))
        return blocks

    completed_ids = {t.id for t in session.tasks if t.status.value == "DONE"}
    counts: dict[str, int] = {}
    lines: list[str] = []

    for t in session.tasks:
        sv = t.status.value
        counts[sv] = counts.get(sv, 0) + 1
        emoji = _STATUS_EMOJI.get(sv, "❓")
        ttype = _TYPE_ABBREV.get(t.type, t.type[:4])
        line = f"{emoji} *{t.id}*  {t.title}  `{ttype} p{t.priority}`"
        if t.depends_on and not t.is_runnable(completed_ids):
            waiting = ", ".join(t.depends_on)
            line += f"  ⏳ _waiting on: {waiting}_"
        lines.append(line)

    blocks.append(_section("\n".join(lines)))

    summary_parts = [f"{v}×{k}" for k, v in counts.items() if v]
    if summary_parts:
        blocks.append(_section("  ".join(summary_parts)))

    if session.hot_memory:
        blocks.append(_divider())
        blocks.append(_section(f"*Hot memory*\n```{session.hot_memory[:500].strip()}```"))

    return blocks


def format_status_text(session: Any) -> str:
    """Plain-text fallback for format_status (for thread replies etc.)."""
    lines: list[str] = [f"📋 *{session.project_name}*"]
    if session.project_description:
        lines.append(f"_{session.project_description}_")
    lines.append("")

    completed_ids = {t.id for t in session.tasks if t.status.value == "DONE"}
    for t in session.tasks:
        emoji = _STATUS_EMOJI.get(t.status.value, "❓")
        ttype = _TYPE_ABBREV.get(t.type, t.type[:4])
        line = f"{emoji} *{t.id}*  {t.title}  `{ttype}`"
        if t.depends_on and not t.is_runnable(completed_ids):
            waiting = ", ".join(t.depends_on)
            line += f"  ⏳ _waiting: {waiting}_"
        lines.append(line)

    return _trunc("\n".join(lines), _MAX_MSG)


def format_task_list(tasks: list[Any]) -> str:
    """Format a flat task list in mrkdwn."""
    if not tasks:
        return "No tasks."
    lines = []
    for t in tasks:
        emoji = _STATUS_EMOJI.get(t.status.value, "❓")
        lines.append(f"{emoji} *{t.id}*  {t.title}")
    return _trunc("\n".join(lines), _MAX_MSG)


def format_recall_results(results: list[dict[str, Any]]) -> str:
    """Format vector recall results in mrkdwn."""
    if not results:
        return "No results found."
    lines = []
    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {})
        rtype = meta.get("type", "note")
        score = 1 - r.get("distance", 1.0)
        ts = meta.get("timestamp", "")[:16].replace("T", " ")
        header = f"*[{i}]* type=`{rtype}`  score={score:.2f}"
        if ts:
            header += f"  _{ts}_"
        lines.append(header)
        lines.append(f"```{r.get('text', '')[:300].strip()}```")
        lines.append("")
    return _trunc("\n".join(lines), _MAX_MSG)


def format_search_results(results: list[dict[str, Any]]) -> str:
    """Format web search results in mrkdwn."""
    if not results:
        return "No results."
    if len(results) == 1 and results[0].get("title") in ("error", ""):
        return f"⚠️ Search error: {results[0].get('snippet', 'unknown error')}"
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        lines.append(f"*[{i}]* {title}")
        if url:
            lines.append(f"<{url}|{url}>")
        if snippet:
            lines.append(snippet[:300])
        lines.append("")
    return _trunc("\n".join(lines), _MAX_MSG)


def format_task_complete(task_id: str, result: str) -> str:
    preview = result[:200].strip() if result else "(no output)"
    return f"✅ *{task_id}* done\n```{preview}```"


def format_task_failed(task_id: str, error: str) -> str:
    preview = str(error)[:200].strip()
    return f"❌ *{task_id}* failed\n```{preview}```"


def format_task_started(task_id: str, title: str) -> str:
    return f"🤖 Starting *{task_id}*: {title}…"


def format_auto_summary(done: list[str], failed: list[str]) -> str:
    lines = ["*Auto run complete.*", ""]
    if done:
        lines.append(f"✅ Done: {', '.join(done)}")
    if failed:
        lines.append(f"❌ Failed: {', '.join(failed)}")
    if not done and not failed:
        lines.append("No tasks were run.")
    return "\n".join(lines)


def format_multi_status(statuses: dict[str, Any]) -> list[dict[str, Any]]:
    """Format aggregate multi-project status as Block Kit blocks."""
    blocks: list[dict[str, Any]] = [_header("📊 Multi-project status")]
    if not statuses:
        blocks.append(_section("No projects running."))
        return blocks

    lines = []
    for project_name, info in statuses.items():
        alive = info.get("alive", False)
        restarts = info.get("restarts", 0)
        icon = "🟢" if alive else "⭕"
        line = f"{icon} *{project_name}*"
        if restarts > 0:
            line += f"  _{restarts} restart(s)_"
        lines.append(line)

    blocks.append(_section("\n".join(lines)))
    return blocks


# ── Notification formatters ────────────────────────────────────────────────────


def format_notification(event: str, data: dict[str, Any]) -> str | None:
    """Format a lifecycle event into a Slack message string.

    Returns None if the event produces no user-visible message.
    """
    if event == "session_start":
        project = data.get("project", "project")
        pending = data.get("pending", 0)
        return f"🌸 *Orchid* session started — {pending} task{'s' if pending != 1 else ''} pending  _{project}_"

    if event == "task_start":
        task_id = data.get("task_id", "?")
        title = data.get("title", "")
        remaining = data.get("remaining")
        msg = f"🤖 *{task_id}* starting — {title}"
        if remaining is not None:
            msg += f"  _{remaining} remaining_"
        return msg

    if event == "task_progress":
        task_id = data.get("task_id", "?")
        iteration = data.get("iter", "?")
        snippet = data.get("thought_snippet", "")
        msg = f"⚙️ *{task_id}* iter {iteration}"
        if snippet:
            msg += f" — _{snippet[:80]}_"
        return msg

    if event == "task_complete":
        task_id = data.get("task_id", "?")
        snippet = data.get("result_snippet", "")
        done_so_far = data.get("done_so_far")
        msg = f"✅ *{task_id}* done"
        if done_so_far is not None:
            msg += f"  _({done_so_far} completed)_"
        if snippet:
            msg += f"\n```{snippet[:200]}```"
        return _trunc(msg, _MAX_MSG)

    if event == "task_failed":
        task_id = data.get("task_id", "?")
        error = data.get("error", "unknown error")
        return _trunc(f"❌ *{task_id}* failed\n```{str(error)[:200]}```", _MAX_MSG)

    if event == "task_blocked":
        task_id = data.get("task_id", "?")
        waiting_on = data.get("waiting_on", [])
        msg = f"⚠️ *{task_id}* blocked"
        if waiting_on:
            msg += f" — waiting on: {', '.join(waiting_on)}"
        return msg

    if event == "session_complete":
        done = data.get("done", [])
        failed = data.get("failed", [])
        total = len(done) + len(failed)
        msg = f"🎉 Session complete — {len(done)}/{total} tasks done"
        if failed:
            msg += f"  _({len(failed)} failed: {', '.join(failed)})_"
        return msg

    return None
