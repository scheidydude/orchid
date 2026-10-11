"""
render_email.py

Rendering functions for each email template type. The LLM never touches
markup — it only ever produces the structured dict payload.

Usage:
    from render_email import render_digest_email, render_prose_email, render_list_email
    html = render_digest_email(digest_dict)

Or from CLI (digest only):
    python render_email.py input.json output.html
"""

import json
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

VERSION = "1.0.0"

_WRAP_WIDTH = 72
_SUBJECT_HARD_MAX = 78  # clients hard-truncate beyond this
_SUBJECT_SOFT_MAX = 60  # recommended for full visibility

TEMPLATE_DIR = Path(__file__).parent / "templates"

_ALERT_LEVELS = {"info", "warning", "error", "success"}
_RECAP_SENTIMENTS = {"positive", "negative", "neutral"}


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def _base_vars(data: dict) -> dict:
    return {
        "subject": data["subject"],
        "preheader": data.get("preheader", ""),
        "intro": data.get("intro", ""),
        "footer_note": data.get("footer_note", ""),
        "unsubscribe_url": data.get("unsubscribe_url", ""),
        "logo_url": data.get("logo_url", ""),
        "logo_alt": data.get("logo_alt", ""),
        "generated_at": datetime.now().strftime("%b %d, %Y %I:%M %p"),
        "server_version": VERSION,
    }


# ---------------------------------------------------------------------------
# Subject length validation (#3)
# ---------------------------------------------------------------------------

def _validate_subject(subject: str) -> None:
    length = len(subject)
    if length > _SUBJECT_HARD_MAX:
        raise ValueError(
            f"Subject is {length} characters — most email clients hard-truncate beyond "
            f"{_SUBJECT_HARD_MAX}. Shorten it before rendering."
        )


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------

def render_digest_email(digest: dict) -> str:
    """Render a digest dict into HTML using digest.html.j2."""
    _validate_digest(digest)
    template = _env().get_template("digest.html.j2")
    return template.render(**_base_vars(digest), sections=digest["sections"])


def _validate_digest(digest: dict) -> None:
    if "subject" not in digest:
        raise ValueError("digest missing required field: subject")
    _validate_subject(digest["subject"])
    if "sections" not in digest or not isinstance(digest["sections"], list):
        raise ValueError("digest missing required field: sections (must be a list)")
    for i, section in enumerate(digest["sections"]):
        if "heading" not in section:
            raise ValueError(f"section {i} missing 'heading'")
        if "items" not in section or not isinstance(section["items"], list):
            raise ValueError(f"section {i} missing 'items' (must be a list)")
        for j, item in enumerate(section["items"]):
            if "title" not in item or "summary" not in item:
                raise ValueError(f"section {i} item {j} missing 'title' or 'summary'")


# ---------------------------------------------------------------------------
# Prose
# ---------------------------------------------------------------------------

def render_prose_email(data: dict) -> str:
    """Render a prose dict into HTML using prose.html.j2."""
    _validate_prose(data)
    template = _env().get_template("prose.html.j2")
    return template.render(
        **_base_vars(data),
        paragraphs=data["paragraphs"],
        cta_text=data.get("cta_text", ""),
        cta_url=data.get("cta_url", ""),
    )


def _validate_prose(data: dict) -> None:
    if "subject" not in data:
        raise ValueError("prose email missing required field: subject")
    _validate_subject(data["subject"])
    if "paragraphs" not in data or not isinstance(data["paragraphs"], list):
        raise ValueError("prose email missing required field: paragraphs (must be a list)")
    if not any(p.strip() for p in data["paragraphs"]):
        raise ValueError("prose email has no non-empty paragraphs")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def render_list_email(data: dict) -> str:
    """Render a list dict into HTML using list.html.j2."""
    _validate_list(data)
    template = _env().get_template("list.html.j2")
    return template.render(
        **_base_vars(data),
        list_style=data.get("list_style", "bullet"),
        items=data["items"],
    )


def _validate_list(data: dict) -> None:
    if "subject" not in data:
        raise ValueError("list email missing required field: subject")
    _validate_subject(data["subject"])
    if "items" not in data or not isinstance(data["items"], list):
        raise ValueError("list email missing required field: items (must be a list)")
    for i, item in enumerate(data["items"]):
        if "name" not in item or "description" not in item:
            raise ValueError(f"list item {i} missing 'name' or 'description'")


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------

def render_alert_email(data: dict) -> str:
    """Render an alert dict into HTML using alert.html.j2."""
    _validate_alert(data)
    template = _env().get_template("alert.html.j2")
    return template.render(
        **_base_vars(data),
        alert_level=data["alert_level"],
        headline=data["headline"],
        body=data["body"],
        details=data.get("details") or [],
        cta_text=data.get("cta_text", ""),
        cta_url=data.get("cta_url", ""),
    )


def _validate_alert(data: dict) -> None:
    if "subject" not in data:
        raise ValueError("alert email missing required field: subject")
    _validate_subject(data["subject"])
    if "alert_level" not in data or data["alert_level"] not in _ALERT_LEVELS:
        raise ValueError(
            f"alert_level must be one of: {sorted(_ALERT_LEVELS)}. Got: {data.get('alert_level')!r}"
        )
    if "headline" not in data or not data["headline"].strip():
        raise ValueError("alert email missing required field: headline")
    if "body" not in data or not data["body"].strip():
        raise ValueError("alert email missing required field: body")


# ---------------------------------------------------------------------------
# Plain-text helpers
# ---------------------------------------------------------------------------

def _pt_header(subject: str) -> str:
    return f"{subject}\n{'=' * min(len(subject), _WRAP_WIDTH)}"


def _pt_footer(data: dict) -> str:
    generated_at = datetime.now().strftime("%b %d, %Y %I:%M %p")
    lines = ["--"]
    if data.get("footer_note"):
        lines.append(data["footer_note"])
    lines.append(f"Generated automatically by Orchid · {generated_at} · v{VERSION}")
    if data.get("unsubscribe_url"):
        lines.append(f"Unsubscribe: {data['unsubscribe_url']}")
    return "\n".join(lines)


def _wrap(text: str, indent: str = "") -> str:
    return textwrap.fill(text, width=_WRAP_WIDTH, initial_indent=indent, subsequent_indent=indent)


# ---------------------------------------------------------------------------
# Digest plaintext
# ---------------------------------------------------------------------------

def render_digest_plaintext(digest: dict) -> str:
    """Render a digest dict as plain text for multipart MIME."""
    _validate_digest(digest)
    parts = [_pt_header(digest["subject"])]

    if digest.get("intro"):
        parts.append("")
        parts.append(_wrap(digest["intro"]))

    for section in digest["sections"]:
        heading = section["heading"].upper()
        parts.append(f"\n{heading}\n{'-' * min(len(heading), _WRAP_WIDTH)}")
        for item in section["items"]:
            parts.append("")
            title = item["title"]
            if item.get("tag"):
                title += f"  [{item['tag'].upper()}]"
            parts.append(_wrap(title))
            parts.append(_wrap(item["summary"], indent="  "))
            if item.get("source"):
                parts.append(f"  {item['source']}")
            if item.get("url"):
                parts.append(f"  {item['url']}")

    parts.append("")
    parts.append(_pt_footer(digest))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Prose plaintext
# ---------------------------------------------------------------------------

def render_prose_plaintext(data: dict) -> str:
    """Render a prose dict as plain text for multipart MIME."""
    _validate_prose(data)
    parts = [_pt_header(data["subject"])]

    if data.get("intro"):
        parts.append("")
        parts.append(_wrap(data["intro"]))

    for para in data["paragraphs"]:
        if para.strip():
            parts.append("")
            parts.append(_wrap(para))

    if data.get("cta_text") and data.get("cta_url"):
        parts.append("")
        parts.append(f">> {data['cta_text']}: {data['cta_url']}")

    parts.append("")
    parts.append(_pt_footer(data))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# List plaintext
# ---------------------------------------------------------------------------

def render_list_plaintext(data: dict) -> str:
    """Render a list dict as plain text for multipart MIME."""
    _validate_list(data)
    parts = [_pt_header(data["subject"])]

    if data.get("intro"):
        parts.append("")
        parts.append(_wrap(data["intro"]))

    numbered = data.get("list_style", "bullet") == "numbered"

    for i, item in enumerate(data["items"], 1):
        parts.append("")
        marker = f"{i}." if numbered else "•"
        name = item["name"]
        if item.get("badge"):
            name += f"  [{item['badge'].upper()}]"
        indent = " " * (len(marker) + 1)
        parts.append(f"{marker} {name}")
        parts.append(_wrap(item["description"], indent=indent))
        if item.get("meta"):
            parts.append(indent + "  ·  ".join(item["meta"]))
        if item.get("url"):
            parts.append(f"{indent}{item['url']}")

    parts.append("")
    parts.append(_pt_footer(data))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Alert plaintext
# ---------------------------------------------------------------------------

def render_alert_plaintext(data: dict) -> str:
    """Render an alert dict as plain text for multipart MIME."""
    _validate_alert(data)
    level = data["alert_level"].upper()
    parts = [f"[{level}] {data['subject']}", "=" * min(len(data["subject"]) + len(level) + 3, _WRAP_WIDTH)]

    parts.append("")
    parts.append(_wrap(data["headline"]))
    parts.append("")
    parts.append(_wrap(data["body"]))

    if data.get("details"):
        parts.append("")
        for fact in data["details"]:
            parts.append(f"  > {fact}")

    if data.get("cta_text") and data.get("cta_url"):
        parts.append("")
        parts.append(f">> {data['cta_text']}: {data['cta_url']}")

    parts.append("")
    parts.append(_pt_footer(data))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Preview (#4)
# ---------------------------------------------------------------------------

_VALIDATORS = {
    "digest": _validate_digest,
    "prose": _validate_prose,
    "list": _validate_list,
    "alert": _validate_alert,
    # "recap" added below after _validate_recap is defined
}


def preview_email_content(template: str, data: dict) -> str:
    """
    Validate data and return a compact text summary of what would render.
    Raises ValueError (with the validation error) if data is invalid.
    """
    if template not in _VALIDATORS:
        raise ValueError(f"Unknown template '{template}'. Choose from: {sorted(_VALIDATORS)}")

    _VALIDATORS[template](data)

    subject = data["subject"]
    char_count = len(subject)
    char_note = (
        f"{char_count} chars — OK"
        if char_count <= _SUBJECT_SOFT_MAX
        else f"{char_count} chars — consider shortening (>{_SUBJECT_SOFT_MAX} may clip in some clients)"
    )

    lines = [
        f"Preview: {template}",
        "-" * 48,
        f"Subject    : {subject}",
        f"             ({char_note})",
    ]

    def _q(text: Any, limit: int = 60) -> str:
        if not text:
            return "—"
        t = str(text)
        clipped = t[:limit] + ("..." if len(t) > limit else "")
        return f'"{clipped}"'

    def _cta(d: dict) -> str:
        if d.get("cta_text") and d.get("cta_url"):
            return f"{d['cta_text']} -> {d['cta_url']}"
        return "—"

    if template == "digest":
        total_items = sum(len(s["items"]) for s in data["sections"])
        section_breakdown = ", ".join(
            f"{s['heading']}: {len(s['items'])}" for s in data["sections"]
        )
        lines += [
            f"Sections   : {len(data['sections'])}",
            f"Items      : {total_items}  ({section_breakdown})",
            f"Intro      : {_q(data.get('intro'))}",
            f"Footer     : {_q(data.get('footer_note'))}",
        ]

    elif template == "prose":
        paras = [p for p in data["paragraphs"] if p.strip()]
        lines += [
            f"Paragraphs : {len(paras)}",
            f"Intro      : {_q(data.get('intro'))}",
            f"CTA        : {_cta(data)}",
            f"Footer     : {_q(data.get('footer_note'))}",
        ]

    elif template == "list":
        style = data.get("list_style", "bullet")
        names = [
            "  {} {}".format("•" if style == "bullet" else f"{i + 1}.", item["name"])
            for i, item in enumerate(data["items"])
        ]
        lines += [f"Style      : {style}", f"Items      : {len(data['items'])}"] + names + [
            f"Intro      : {_q(data.get('intro'))}",
        ]

    elif template == "alert":
        body_preview = data["body"][:80] + ("..." if len(data["body"]) > 80 else "")
        lines += [
            f"Level      : {data['alert_level'].upper()}",
            f"Headline   : {data['headline']}",
            f"Body       : {body_preview}",
            f"Details    : {len(data.get('details') or [])} item(s)",
            f"CTA        : {_cta(data)}",
        ]

    elif template == "recap":
        metrics_summary = ", ".join(
            f"{m['label']}: {m['value']}" for m in data["metrics"]
        )
        lines += [
            f"Period     : {data.get('period', '—')}",
            f"Metrics    : {len(data['metrics'])}  ({metrics_summary})",
            f"Highlights : {len(data.get('highlights') or [])}",
            f"Sections   : {len(data.get('sections') or [])}",
            f"Intro      : {_q(data.get('intro'))}",
        ]

    lines.append("-" * 48)
    lines.append("Validation : OK")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Emoji stripping (#11)
# ---------------------------------------------------------------------------

import re as _re

_EMOJI_RE = _re.compile(
    "["
    "\U0001F000-\U0001FFFF"   # Supplementary Multilingual Plane (faces, objects, symbols)
    "\U00002700-\U000027BF"   # Dingbats
    "\U00002600-\U000026FF"   # Misc symbols (sun, snowflake, star, etc.)
    "\U00002300-\U000023FF"   # Misc technical (clocks, hourglasses, etc.)
    "\U0001F1E0-\U0001F1FF"   # Regional indicator symbols (flags)
    "]",
    flags=_re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()


def strip_subject_emoji(subject: str) -> tuple[str, list[str]]:
    """
    Strip emoji from a subject line.
    Returns (stripped_subject, list_of_removed_chars).
    """
    removed = _EMOJI_RE.findall(subject)
    return _strip_emoji(subject), removed


# ---------------------------------------------------------------------------
# Recap (#12)
# ---------------------------------------------------------------------------

def render_recap_email(data: dict) -> str:
    """Render a weekly/periodic recap into HTML using recap.html.j2."""
    _validate_recap(data)
    template = _env().get_template("recap.html.j2")
    return template.render(
        **_base_vars(data),
        period=data.get("period", ""),
        metrics=data["metrics"],
        highlights=data.get("highlights") or [],
        sections=data.get("sections") or [],
    )


def _validate_recap(data: dict) -> None:
    if "subject" not in data:
        raise ValueError("recap email missing required field: subject")
    _validate_subject(data["subject"])
    if "metrics" not in data or not isinstance(data["metrics"], list):
        raise ValueError("recap email missing required field: metrics (must be a list)")
    if not data["metrics"]:
        raise ValueError("recap email requires at least one metric")
    if len(data["metrics"]) > 4:
        raise ValueError(f"recap email supports up to 4 metrics; got {len(data['metrics'])}")
    for i, m in enumerate(data["metrics"]):
        if "label" not in m or "value" not in m:
            raise ValueError(f"metric {i} missing 'label' or 'value'")
        if m.get("delta_sentiment") and m["delta_sentiment"] not in _RECAP_SENTIMENTS:
            raise ValueError(
                f"metric {i} delta_sentiment must be one of {sorted(_RECAP_SENTIMENTS)}; "
                f"got {m['delta_sentiment']!r}"
            )
    for i, h in enumerate(data.get("highlights") or []):
        if "text" not in h:
            raise ValueError(f"highlight {i} missing 'text'")
    for i, s in enumerate(data.get("sections") or []):
        if "heading" not in s or "items" not in s:
            raise ValueError(f"recap section {i} missing 'heading' or 'items'")


_VALIDATORS["recap"] = _validate_recap


def render_recap_plaintext(data: dict) -> str:
    """Render a recap dict as plain text for multipart MIME."""
    _validate_recap(data)
    subject = data["subject"]
    period = data.get("period", "")
    parts = [_pt_header(subject)]
    if period:
        parts.append(period)

    if data.get("intro"):
        parts.append("")
        parts.append(_wrap(data["intro"]))

    parts.append("")
    parts.append("METRICS")
    parts.append("-" * 7)
    for m in data["metrics"]:
        delta_str = ""
        if m.get("delta"):
            sentiment = m.get("delta_sentiment", "neutral")
            indicator = "▲" if sentiment == "positive" else "▼" if sentiment == "negative" else "—"
            delta_str = f"  {indicator} {m['delta']}"
        parts.append(f"  {m['label']}: {m['value']}{delta_str}")

    if data.get("highlights"):
        parts.append("")
        parts.append("HIGHLIGHTS")
        parts.append("-" * 10)
        for h in data["highlights"]:
            tag_str = f"  [{h['tag'].upper()}]" if h.get("tag") else ""
            parts.append(f"  * {h['text']}{tag_str}")

    for section in (data.get("sections") or []):
        heading = section["heading"].upper()
        parts.append(f"\n{heading}\n{'-' * min(len(heading), _WRAP_WIDTH)}")
        for item in section["items"]:
            parts.append(f"  - {item}")

    parts.append("")
    parts.append(_pt_footer(data))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Schema introspection (#10)
# ---------------------------------------------------------------------------

_TEMPLATE_SCHEMAS: dict[str, str] = {
    "digest": """\
digest — multi-section card layout
===================================
Required
  subject              str    Subject line. Max 78 chars.
  sections             list   One or more content sections.
    .heading           str    Section heading.
    .items             list   Items in this section.
      .title           str    Short headline, no markdown.
      .summary         str    1–3 sentence plain-text summary.

Optional (top-level)
  preheader            str    Hidden inbox preview text.
  intro                str    One-line framing shown below the header.
  footer_note          str    Closing line in the footer.
  logo_url             str    Publicly hosted logo image URL (use light/white logo).
  logo_alt             str    Alt text for logo image.
  unsubscribe_url      str    URL rendered as an Unsubscribe link in the footer.
  strip_subject_emoji  bool   Strip emoji from subject before rendering. Default false.

Optional (per item)
  .url                 str    Source link.
  .source              str    Publication or site name.
  .tag                 str    Short badge label, e.g. "new" or "security".\
""",

    "prose": """\
prose — flowing paragraph layout
==================================
Required
  subject              str    Subject line. Max 78 chars.
  paragraphs           list   One or more body paragraphs (each a plain-text string).

Optional
  preheader            str    Hidden inbox preview text.
  intro                str    Bold lede shown above the body paragraphs.
  cta_text             str    Call-to-action button label. Requires cta_url.
  cta_url              str    Call-to-action button URL. Requires cta_text.
  footer_note          str    Closing line in the footer.
  logo_url             str    Publicly hosted logo image URL (use light/white logo).
  logo_alt             str    Alt text for logo image.
  unsubscribe_url      str    URL rendered as an Unsubscribe link in the footer.
  strip_subject_emoji  bool   Strip emoji from subject before rendering. Default false.\
""",

    "list": """\
list — bullet or numbered list layout
=======================================
Required
  subject              str    Subject line. Max 78 chars.
  items                list   One or more list items.
    .name              str    Item name or short title, no markdown.
    .description       str    1–3 sentence plain-text description.

Optional (top-level)
  list_style           str    "bullet" (default) or "numbered".
  preheader            str    Hidden inbox preview text.
  intro                str    One-line framing shown below the header.
  footer_note          str    Closing line in the footer.
  logo_url             str    Publicly hosted logo image URL (use light/white logo).
  logo_alt             str    Alt text for logo image.
  unsubscribe_url      str    URL rendered as an Unsubscribe link in the footer.
  strip_subject_emoji  bool   Strip emoji from subject before rendering. Default false.

Optional (per item)
  .url                 str    Link for this item.
  .badge               str    Short badge label, e.g. "new" or "recommended".
  .meta                list   Short fact strings, e.g. ["Author: Jane", "June 2026"].\
""",

    "alert": """\
alert — single urgent message layout
======================================
Required
  subject              str    Subject line. Max 78 chars.
  alert_level          str    One of: "error", "warning", "info", "success".
  headline             str    Large bold headline shown prominently.
  body                 str    Explanation paragraph below the headline.

Optional
  details              list   Short fact strings shown as a sub-list, e.g. ["Region: us-east-1"].
  preheader            str    Hidden inbox preview text.
  cta_text             str    Call-to-action button label. Requires cta_url.
  cta_url              str    Call-to-action button URL. Requires cta_text.
  footer_note          str    Closing line in the footer.
  logo_url             str    Publicly hosted logo image URL (use light/white logo on accent bg).
  logo_alt             str    Alt text for logo image.
  unsubscribe_url      str    URL rendered as an Unsubscribe link in the footer.
  strip_subject_emoji  bool   Strip emoji from subject before rendering. Default false.\
""",

    "recap": """\
recap — periodic metrics + highlights layout
=============================================
Required
  subject              str    Subject line. Max 78 chars.
  metrics              list   1–4 metric cards shown at the top. Each metric:
    .label             str    Metric name, e.g. "Total Searches".
    .value             str    Current value, e.g. "1,234" or "98.2%".

Optional (top-level)
  period               str    Period label, e.g. "Week of June 15–21, 2026".
  highlights           list   Notable events or achievements. Each highlight:
    .text              str    1–2 sentence highlight text.
    .tag               str    Short badge label, e.g. "shipped" or "fixed".
  sections             list   Detailed breakdown sections. Each section:
    .heading           str    Section heading.
    .items             list   Plain-text bullet strings.
  preheader            str    Hidden inbox preview text.
  intro                str    One-line framing shown below the header.
  footer_note          str    Closing line in the footer.
  logo_url             str    Publicly hosted logo image URL (use light/white logo).
  logo_alt             str    Alt text for logo image.
  unsubscribe_url      str    URL rendered as an Unsubscribe link in the footer.
  strip_subject_emoji  bool   Strip emoji from subject before rendering. Default false.

Optional (per metric)
  .delta               str    Change indicator, e.g. "+12%" or "-3 items".
  .delta_sentiment     str    One of: "positive", "negative", "neutral" (default).
                              Controls delta colour (green / red / gray).\
""",
}


def get_schema(template: str) -> str:
    """Return the field reference for a given template name."""
    if template not in _TEMPLATE_SCHEMAS:
        raise ValueError(
            f"Unknown template '{template}'. Available: {sorted(_TEMPLATE_SCHEMAS)}"
        )
    return _TEMPLATE_SCHEMAS[template]


# ---------------------------------------------------------------------------
# CLI (digest only)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python render_email.py input.json output.html")
        sys.exit(1)

    input_path, output_path = sys.argv[1], sys.argv[2]
    with open(input_path) as f:
        digest = json.load(f)

    html = render_digest_email(digest)

    with open(output_path, "w") as f:
        f.write(html)

    print(f"Rendered {input_path} -> {output_path}")
