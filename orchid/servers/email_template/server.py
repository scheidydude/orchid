"""
server.py — MCP server exposing email-rendering as tools.

Run standalone for testing:
    python server.py

Add to Orchid / Claude config as a stdio MCP server, e.g.:
    {
      "mcpServers": {
        "email-render": {
          "command": "python",
          "args": ["/path/to/email-render-mcp/server.py"]
        }
      }
    }

Exposes one tool per template type — the agent picks the right one based on
the content it has, and gets a fully typed schema from the tool definition:

  - render_digest_email      sections of rich item cards (title/summary/url/source/tag)
  - render_prose_email       flowing body paragraphs with an optional CTA button
  - render_list_email        simple bullet or numbered list (name/description/url/badge/meta)
  - render_alert_email       single urgent alert with level, headline, body, details, CTA
  - render_recap_email       periodic metrics + highlights recap (metrics, highlights, sections)
  - render_digest_plaintext  plain-text companion to render_digest_email
  - render_prose_plaintext   plain-text companion to render_prose_email
  - render_list_plaintext    plain-text companion to render_list_email
  - render_alert_plaintext   plain-text companion to render_alert_email
  - render_recap_plaintext   plain-text companion to render_recap_email
  - preview_email            validate + summarise what would render before committing to send
  - get_template_schema      return the field reference for a given template
  - sanitize_subject         strip emoji from a subject line and report what was removed
  - list_email_templates     informational: lists available .j2 files in templates/

The agent should call the appropriate render_* tool as the LAST step before
calling its email-send tool. It must never write HTML itself.

Environment variables:
  RENDER_ONLY      Set to '1' or 'true' to write rendered HTML to disk instead
                   of returning it — prevents accidental sends in CI pipelines.
  RENDER_ONLY_DIR  Directory for RENDER_ONLY output (default: /tmp/email-render-preview/).
"""

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))
from render_email import (  # noqa: E402
    get_schema as _get_schema,
)
from render_email import (
    preview_email_content as _preview,
)
from render_email import (
    render_alert_email as _render_alert,
)
from render_email import (
    render_alert_plaintext as _render_alert_pt,
)
from render_email import (
    render_digest_email as _render_digest,
)
from render_email import (
    render_digest_plaintext as _render_digest_pt,
)
from render_email import (
    render_list_email as _render_list,
)
from render_email import (
    render_list_plaintext as _render_list_pt,
)
from render_email import (
    render_prose_email as _render_prose,
)
from render_email import (
    render_prose_plaintext as _render_prose_pt,
)
from render_email import (
    render_recap_email as _render_recap,
)
from render_email import (
    render_recap_plaintext as _render_recap_pt,
)
from render_email import (
    strip_subject_emoji as _strip_emoji,
)

mcp = FastMCP("email-render")

TEMPLATE_DIR = Path(__file__).parent / "templates"

# ---------------------------------------------------------------------------
# Dry-run / CI mode
# ---------------------------------------------------------------------------

_RENDER_ONLY = os.environ.get("RENDER_ONLY", "").lower() in ("1", "true", "yes")
_RENDER_ONLY_DIR = Path(os.environ.get("RENDER_ONLY_DIR", "/tmp/email-render-preview"))


def _maybe_dry_run(html: str, template_name: str) -> str:
    if not _RENDER_ONLY:
        return html
    _RENDER_ONLY_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = _RENDER_ONLY_DIR / f"{template_name}_{ts}.html"
    path.write_text(html)
    return (
        f"[RENDER_ONLY] HTML written to: {path}\n"
        "Do not pass this output to an email-send tool."
    )


# ---------------------------------------------------------------------------
# File output mode
# ---------------------------------------------------------------------------

# Orchid: default to file output (local LLMs cannot reliably JSON-encode big HTML).
os.environ.setdefault("RENDER_OUTPUT", "file")
_DEFAULT_OUTPUT = os.environ.get("RENDER_OUTPUT", "html").lower()
if _DEFAULT_OUTPUT not in ("html", "file"):
    _DEFAULT_OUTPUT = "html"


def _deliver(html: str, template_name: str, output: str | None) -> str:
    """
    Return rendered HTML, or write it to a temp file and return a message with
    the path (output="file"). File mode suits local LLMs that cannot reliably
    JSON-encode multi-KB HTML into a send-tool argument: the agent passes the
    path as html_body_file instead. RENDER_ONLY takes precedence.
    """
    if _RENDER_ONLY:
        return _maybe_dry_run(html, template_name)
    mode = (output or _DEFAULT_OUTPUT).lower()
    if mode == "html":
        return html
    if mode != "file":
        raise ValueError(f"Unknown output mode '{output}'. Use 'html' or 'file'.")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", prefix=f"email_{template_name}_", delete=False, encoding="utf-8"
    ) as f:
        f.write(html)
        path = f.name
    return (
        f"Email rendered successfully ({len(html)} bytes).\n"
        f"HTML saved to: {path}\n"
        f'Pass html_body_file="{path}" to the send tool -- do NOT pass html_body.'
    )


# ---------------------------------------------------------------------------
# Subject helpers
# ---------------------------------------------------------------------------

def _prep_subject(subject: str, strip_emoji: bool) -> str:
    if strip_emoji:
        subject, _ = _strip_emoji(subject)
    return subject


# ---------------------------------------------------------------------------
# Digest models
# ---------------------------------------------------------------------------

class DigestItem(BaseModel):
    title: str = Field(..., description="Short headline, no markdown.")
    summary: str = Field(..., description="1-3 sentence plain-text summary.")
    url: str | None = Field(None, description="Source link, if any.")
    source: str | None = Field(None, description="Publication or site name.")
    tag: str | None = Field(None, description="Short label e.g. 'new' or 'security'. Omit if not applicable.")


class DigestSection(BaseModel):
    heading: str = Field(..., description="Section heading, e.g. 'MCP & Agent Tooling'.")
    items: list[DigestItem] = Field(..., description="Items in this section.")


# ---------------------------------------------------------------------------
# List models
# ---------------------------------------------------------------------------

class ListItem(BaseModel):
    name: str = Field(..., description="Item name or short title, no markdown.")
    description: str = Field(..., description="1-3 sentence plain-text description.")
    url: str | None = Field(None, description="Link for this item, if any.")
    badge: str | None = Field(None, description="Short label e.g. 'new', 'recommended'. Omit if not applicable.")
    meta: list[str] | None = Field(None, description="Additional short facts, e.g. ['Author: Jane', 'June 2026']. Omit if not applicable.")


# ---------------------------------------------------------------------------
# Recap models
# ---------------------------------------------------------------------------

class RecapMetric(BaseModel):
    label: str = Field(..., description="Metric name, e.g. 'Total Searches'.")
    value: str = Field(..., description="Current value, e.g. '1,234' or '98.2%'.")
    delta: str | None = Field(None, description="Change indicator, e.g. '+12%' or '-3 items'. Omit if not applicable.")
    delta_sentiment: Literal["positive", "negative", "neutral"] | None = Field(
        None,
        description="Controls delta colour: 'positive' (green ▲), 'negative' (red ▼), 'neutral' (gray —). Default neutral.",
    )


class RecapHighlight(BaseModel):
    text: str = Field(..., description="1-2 sentence highlight, plain text.")
    tag: str | None = Field(None, description="Short badge label e.g. 'shipped', 'fixed'. Omit if not applicable.")


class RecapSection(BaseModel):
    heading: str = Field(..., description="Section heading.")
    items: list[str] = Field(..., description="Plain-text bullet strings.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def render_digest_email(
    subject: str,
    sections: list[DigestSection],
    preheader: str | None = None,
    intro: str | None = None,
    footer_note: str | None = None,
    logo_url: str | None = None,
    logo_alt: str | None = None,
    unsubscribe_url: str | None = None,
    strip_subject_emoji: bool = False,
    output: Literal["html", "file"] | None = None,
) -> str:
    """
    Render a multi-section digest into a polished HTML email.

    Use when content naturally organises into named sections, each containing
    multiple items with a title, summary, source, and optional link/tag.
    Ideal for news digests, research roundups, and curated link lists.

    logo_url: publicly hosted image URL rendered above the subject in the
    header bar. Use a light/white logo — the header background is dark.
    logo_alt: alt text for the logo image (recommended for accessibility).
    unsubscribe_url: if provided, an 'Unsubscribe' link is added to the footer.
    strip_subject_emoji: if true, emoji are stripped from the subject before rendering.

    output: "html" (default) returns the HTML string. "file" writes it to a temp
    file and returns the path -- pass that as html_body_file to the send tool
    instead of embedding HTML in the call. Default overridable via RENDER_OUTPUT.

    Returns the final HTML string to pass as the email body (or the file path
    message when output="file").
    """
    if not sections or all(len(s.items) == 0 for s in sections):
        raise ValueError(
            "No items to render. If the search step found nothing, send a short "
            "plain-text 'no updates today' email instead of an empty digest."
        )

    digest = {
        "subject": _prep_subject(subject, strip_subject_emoji),
        "preheader": preheader or "",
        "intro": intro or "",
        "sections": [s.model_dump() for s in sections],
        "footer_note": footer_note or "",
        "logo_url": logo_url or "",
        "logo_alt": logo_alt or "",
        "unsubscribe_url": unsubscribe_url or "",
    }
    return _deliver(_render_digest(digest), "digest", output)


@mcp.tool()
def render_prose_email(
    subject: str,
    paragraphs: list[str],
    preheader: str | None = None,
    intro: str | None = None,
    cta_text: str | None = None,
    cta_url: str | None = None,
    footer_note: str | None = None,
    logo_url: str | None = None,
    logo_alt: str | None = None,
    unsubscribe_url: str | None = None,
    strip_subject_emoji: bool = False,
    output: Literal["html", "file"] | None = None,
) -> str:
    """
    Render a flowing prose email with optional call-to-action button.

    Use when the body is a single coherent message — an announcement,
    a summary write-up, a narrative update, or any email where sectioned
    cards would feel overly structured.

    paragraphs: each string becomes its own <p>. Pass plain text only —
    no HTML or markdown. Use separate list entries for paragraph breaks.

    cta_text / cta_url: if both provided, a prominent button is rendered
    below the paragraphs. Omit both if not needed.

    logo_url: publicly hosted image URL rendered above the subject in the
    header bar. Use a light/white logo — the header background is dark.
    logo_alt: alt text for the logo image (recommended for accessibility).
    unsubscribe_url: if provided, an 'Unsubscribe' link is added to the footer.
    strip_subject_emoji: if true, emoji are stripped from the subject before rendering.

    output: "html" (default) returns the HTML string. "file" writes it to a temp
    file and returns the path -- pass that as html_body_file to the send tool
    instead of embedding HTML in the call. Default overridable via RENDER_OUTPUT.

    Returns the final HTML string to pass as the email body (or the file path
    message when output="file").
    """
    if not paragraphs or all(p.strip() == "" for p in paragraphs):
        raise ValueError(
            "No body content to render. Provide at least one non-empty paragraph."
        )

    data = {
        "subject": _prep_subject(subject, strip_subject_emoji),
        "preheader": preheader or "",
        "intro": intro or "",
        "paragraphs": [p for p in paragraphs if p.strip()],
        "cta_text": cta_text or "",
        "cta_url": cta_url or "",
        "footer_note": footer_note or "",
        "logo_url": logo_url or "",
        "logo_alt": logo_alt or "",
        "unsubscribe_url": unsubscribe_url or "",
    }
    return _deliver(_render_prose(data), "prose", output)


@mcp.tool()
def render_list_email(
    subject: str,
    items: list[ListItem],
    list_style: Literal["bullet", "numbered"] = "bullet",
    preheader: str | None = None,
    intro: str | None = None,
    footer_note: str | None = None,
    logo_url: str | None = None,
    logo_alt: str | None = None,
    unsubscribe_url: str | None = None,
    strip_subject_emoji: bool = False,
    output: Literal["html", "file"] | None = None,
) -> str:
    """
    Render a bullet or numbered list email.

    Use when content is a flat sequence of items — recommendations,
    action items, feature lists, or any enumeration where section grouping
    (digest) would be overkill and prose paragraphs don't fit.

    list_style: 'bullet' for • markers, 'numbered' for 1. 2. 3. markers.

    Each item has a name (title), description (1-3 sentences), and optional
    url, badge label, and meta strings (e.g. ['Author: Jane', 'June 2026']).

    logo_url: publicly hosted image URL rendered above the subject in the
    header bar. Use a light/white logo — the header background is dark.
    logo_alt: alt text for the logo image (recommended for accessibility).
    unsubscribe_url: if provided, an 'Unsubscribe' link is added to the footer.
    strip_subject_emoji: if true, emoji are stripped from the subject before rendering.

    output: "html" (default) returns the HTML string. "file" writes it to a temp
    file and returns the path -- pass that as html_body_file to the send tool
    instead of embedding HTML in the call. Default overridable via RENDER_OUTPUT.

    Returns the final HTML string to pass as the email body (or the file path
    message when output="file").
    """
    if not items:
        raise ValueError("No items to render. Provide at least one list item.")

    data = {
        "subject": _prep_subject(subject, strip_subject_emoji),
        "preheader": preheader or "",
        "intro": intro or "",
        "list_style": list_style,
        "items": [i.model_dump() for i in items],
        "footer_note": footer_note or "",
        "logo_url": logo_url or "",
        "logo_alt": logo_alt or "",
        "unsubscribe_url": unsubscribe_url or "",
    }
    return _deliver(_render_list(data), "list", output)


@mcp.tool()
def render_alert_email(
    subject: str,
    alert_level: Literal["info", "warning", "error", "success"],
    headline: str,
    body: str,
    details: list[str] | None = None,
    preheader: str | None = None,
    cta_text: str | None = None,
    cta_url: str | None = None,
    footer_note: str | None = None,
    logo_url: str | None = None,
    logo_alt: str | None = None,
    unsubscribe_url: str | None = None,
    strip_subject_emoji: bool = False,
    output: Literal["html", "file"] | None = None,
) -> str:
    """
    Render a single urgent alert email.

    Use for incident notifications, threshold breaches, system events, or
    any message where a single high-signal headline matters more than
    structured sections or flowing prose.

    alert_level controls the colour scheme:
      'error'   — red    (outages, failures, critical issues)
      'warning' — amber  (degraded performance, approaching limits)
      'info'    — blue   (deployments, announcements, FYIs)
      'success' — green  (recovery, completion, positive outcomes)

    details: optional list of short fact strings shown as a bulleted
    sub-list below the body, e.g. ['Region: us-east-1', 'Duration: 4m 32s'].

    logo_url: publicly hosted image URL rendered above the subject in the
    header bar. For alert emails use a light/white logo — the header is the
    alert accent colour.
    logo_alt: alt text for the logo image (recommended for accessibility).
    unsubscribe_url: if provided, an 'Unsubscribe' link is added to the footer.
    strip_subject_emoji: if true, emoji are stripped from the subject before rendering.

    output: "html" (default) returns the HTML string. "file" writes it to a temp
    file and returns the path -- pass that as html_body_file to the send tool
    instead of embedding HTML in the call. Default overridable via RENDER_OUTPUT.

    Returns the final HTML string to pass as the email body (or the file path
    message when output="file").
    """
    data = {
        "subject": _prep_subject(subject, strip_subject_emoji),
        "preheader": preheader or "",
        "alert_level": alert_level,
        "headline": headline,
        "body": body,
        "details": details or [],
        "cta_text": cta_text or "",
        "cta_url": cta_url or "",
        "footer_note": footer_note or "",
        "logo_url": logo_url or "",
        "logo_alt": logo_alt or "",
        "unsubscribe_url": unsubscribe_url or "",
    }
    return _deliver(_render_alert(data), "alert", output)


@mcp.tool()
def render_recap_email(
    subject: str,
    metrics: list[RecapMetric],
    period: str | None = None,
    highlights: list[RecapHighlight] | None = None,
    sections: list[RecapSection] | None = None,
    preheader: str | None = None,
    intro: str | None = None,
    footer_note: str | None = None,
    logo_url: str | None = None,
    logo_alt: str | None = None,
    unsubscribe_url: str | None = None,
    strip_subject_emoji: bool = False,
    output: Literal["html", "file"] | None = None,
) -> str:
    """
    Render a periodic recap email with metric cards and optional highlights.

    Use for weekly/monthly summaries, performance reports, or any recurring
    email where numbers and achievements take center stage.

    metrics: 1–4 metric cards displayed prominently at the top. Each has a
    label, a value, and an optional delta with sentiment colouring
    (positive=green ▲, negative=red ▼, neutral=gray —).

    highlights: notable events or achievements, rendered as a bullet list
    below the metrics. Each has a text string and an optional tag badge.

    sections: optional detailed breakdown sections, each with a heading and
    a list of plain-text bullet strings.

    period: optional period label shown below the subject in the header,
    e.g. 'Week of June 15–21, 2026'.

    logo_url: publicly hosted image URL rendered above the subject in the
    header bar. Use a light/white logo — the header background is dark.
    logo_alt: alt text for the logo image (recommended for accessibility).
    unsubscribe_url: if provided, an 'Unsubscribe' link is added to the footer.
    strip_subject_emoji: if true, emoji are stripped from the subject before rendering.

    output: "html" (default) returns the HTML string. "file" writes it to a temp
    file and returns the path -- pass that as html_body_file to the send tool
    instead of embedding HTML in the call. Default overridable via RENDER_OUTPUT.

    Returns the final HTML string to pass as the email body (or the file path
    message when output="file").
    """
    if not metrics:
        raise ValueError("No metrics to render. Provide at least one metric.")

    data = {
        "subject": _prep_subject(subject, strip_subject_emoji),
        "period": period or "",
        "preheader": preheader or "",
        "intro": intro or "",
        "metrics": [m.model_dump() for m in metrics],
        "highlights": [h.model_dump() for h in (highlights or [])],
        "sections": [s.model_dump() for s in (sections or [])],
        "footer_note": footer_note or "",
        "logo_url": logo_url or "",
        "logo_alt": logo_alt or "",
        "unsubscribe_url": unsubscribe_url or "",
    }
    return _deliver(_render_recap(data), "recap", output)


@mcp.tool()
def render_digest_plaintext(
    subject: str,
    sections: list[DigestSection],
    intro: str | None = None,
    footer_note: str | None = None,
    unsubscribe_url: str | None = None,
) -> str:
    """
    Render a digest as plain text for use as the text/plain part of a
    multipart MIME email. Accepts the same fields as render_digest_email
    (minus preheader and logo, which are HTML-only).
    """
    if not sections or all(len(s.items) == 0 for s in sections):
        raise ValueError("No items to render. Provide at least one section with items.")

    digest = {
        "subject": subject,
        "intro": intro or "",
        "sections": [s.model_dump() for s in sections],
        "footer_note": footer_note or "",
        "unsubscribe_url": unsubscribe_url or "",
    }
    return _render_digest_pt(digest)


@mcp.tool()
def render_prose_plaintext(
    subject: str,
    paragraphs: list[str],
    intro: str | None = None,
    cta_text: str | None = None,
    cta_url: str | None = None,
    footer_note: str | None = None,
    unsubscribe_url: str | None = None,
) -> str:
    """
    Render a prose email as plain text for use as the text/plain part of a
    multipart MIME email. CTA renders as '>> Label: https://...' on its own line.
    """
    if not paragraphs or all(p.strip() == "" for p in paragraphs):
        raise ValueError("No body content to render. Provide at least one non-empty paragraph.")

    data = {
        "subject": subject,
        "intro": intro or "",
        "paragraphs": [p for p in paragraphs if p.strip()],
        "cta_text": cta_text or "",
        "cta_url": cta_url or "",
        "footer_note": footer_note or "",
        "unsubscribe_url": unsubscribe_url or "",
    }
    return _render_prose_pt(data)


@mcp.tool()
def render_list_plaintext(
    subject: str,
    items: list[ListItem],
    list_style: Literal["bullet", "numbered"] = "bullet",
    intro: str | None = None,
    footer_note: str | None = None,
    unsubscribe_url: str | None = None,
) -> str:
    """
    Render a list email as plain text for use as the text/plain part of a
    multipart MIME email. Bullets render as '• Name', numbered as '1. Name'.
    """
    if not items:
        raise ValueError("No items to render. Provide at least one list item.")

    data = {
        "subject": subject,
        "intro": intro or "",
        "list_style": list_style,
        "items": [i.model_dump() for i in items],
        "footer_note": footer_note or "",
        "unsubscribe_url": unsubscribe_url or "",
    }
    return _render_list_pt(data)


@mcp.tool()
def render_alert_plaintext(
    subject: str,
    alert_level: Literal["info", "warning", "error", "success"],
    headline: str,
    body: str,
    details: list[str] | None = None,
    cta_text: str | None = None,
    cta_url: str | None = None,
    footer_note: str | None = None,
    unsubscribe_url: str | None = None,
) -> str:
    """
    Render an alert as plain text for use as the text/plain part of a
    multipart MIME email. Level renders as '[ERROR]', '[WARNING]', etc.
    """
    data = {
        "subject": subject,
        "alert_level": alert_level,
        "headline": headline,
        "body": body,
        "details": details or [],
        "cta_text": cta_text or "",
        "cta_url": cta_url or "",
        "footer_note": footer_note or "",
        "unsubscribe_url": unsubscribe_url or "",
    }
    return _render_alert_pt(data)


@mcp.tool()
def render_recap_plaintext(
    subject: str,
    metrics: list[RecapMetric],
    period: str | None = None,
    highlights: list[RecapHighlight] | None = None,
    sections: list[RecapSection] | None = None,
    intro: str | None = None,
    footer_note: str | None = None,
    unsubscribe_url: str | None = None,
) -> str:
    """
    Render a recap email as plain text for use as the text/plain part of a
    multipart MIME email. Metrics render as 'Label: Value  ▲ delta'.
    """
    if not metrics:
        raise ValueError("No metrics to render. Provide at least one metric.")

    data = {
        "subject": subject,
        "period": period or "",
        "intro": intro or "",
        "metrics": [m.model_dump() for m in metrics],
        "highlights": [h.model_dump() for h in (highlights or [])],
        "sections": [s.model_dump() for s in (sections or [])],
        "footer_note": footer_note or "",
        "unsubscribe_url": unsubscribe_url or "",
    }
    return _render_recap_pt(data)


@mcp.tool()
def preview_email(
    template: Literal["digest", "prose", "list", "alert", "recap"],
    data: dict,
) -> str:
    """
    Validate email data and return a compact text summary of what would
    render — without producing HTML. Call this before render_* + send to
    confirm subject length, item counts, CTA presence, and data validity.

    template: which render tool this data targets.
    data: the same dict you would pass to the corresponding render tool.

    Returns a human-readable summary on success, or raises ValueError with
    the exact validation error on failure.
    """
    return _preview(template, data)


@mcp.tool()
def get_template_schema(
    template: Literal["digest", "prose", "list", "alert", "recap"],
) -> str:
    """
    Return the field reference for a given template — required fields,
    optional fields, types, and descriptions.

    Use this when you need a reminder of what data to pass to a render tool,
    or to share schema documentation with another system.
    """
    return _get_schema(template)


@mcp.tool()
def sanitize_subject(subject: str) -> dict:
    """
    Strip emoji from a subject line and report what was removed.

    Use before calling a render tool if you're unsure whether emoji in the
    subject will display correctly across all email clients (notably Outlook
    desktop mangles some emoji).

    Returns a dict with:
      sanitized  — the subject with emoji removed
      removed    — list of emoji characters that were stripped
      original_length   — character count before stripping
      sanitized_length  — character count after stripping
      changed    — true if any emoji were removed
    """
    cleaned, removed = _strip_emoji(subject)
    return {
        "sanitized": cleaned,
        "removed": removed,
        "original_length": len(subject),
        "sanitized_length": len(cleaned),
        "changed": bool(removed),
    }


@mcp.tool()
def list_email_templates() -> list[str]:
    """
    List available .j2 template files. Informational — you do not pass
    template names to the render tools; each tool uses its own fixed template.
    Use this to confirm which templates are installed.
    """
    return sorted(p.name for p in TEMPLATE_DIR.glob("*.j2"))


# ---------------------------------------------------------------------------
# Orchid-specific: backward-compat alias + console-script entry point
# ---------------------------------------------------------------------------

@mcp.tool()
def render_email(
    subject: str,
    sections: list[DigestSection],
    preheader: str | None = None,
    intro: str | None = None,
    footer_note: str | None = None,
    template: str = "digest.html.j2",
) -> str:
    """
    Deprecated alias for render_digest_email (kept for existing prompts).
    Returns a file path message; pass the path as html_body_file to send_email.
    """
    if template != "digest.html.j2":
        raise ValueError(
            f"render_email only supports digest.html.j2; got '{template}'. "
            "Use the render_<type>_email tools for other templates."
        )
    return render_digest_email(
        subject=subject, sections=sections, preheader=preheader,
        intro=intro, footer_note=footer_note,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
