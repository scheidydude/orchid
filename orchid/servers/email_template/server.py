"""
Orchid built-in email-template MCP server.

Exposes two tools:
  - render_email: turns a structured digest (JSON) into polished HTML.
  - list_email_templates: lets the agent discover which templates exist.

The agent should call render_email as the LAST step before calling its
email-send tool. It must never write HTML itself -- only the structured
fields below.

Usage
-----
Run directly::

    orchid-mcp-email-template

Or reference in mcp_catalog.json::

    {
      "command": ["orchid-mcp-email-template"]
    }
"""

import sys
import tempfile
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))
from render_email import render_digest_email, _validate  # noqa: E402

mcp = FastMCP("email-render")

TEMPLATE_DIR = Path(__file__).parent / "templates"


class EmailItem(BaseModel):
    title: str = Field(..., description="Short headline for this item, no markdown.", max_length=150)
    summary: str = Field(..., description="1-3 sentence plain-text summary. Max ~300 chars. Do NOT paste raw article text.", max_length=500)
    url: Optional[str] = Field(None, description="Source link, if any.", max_length=500)
    source: Optional[str] = Field(None, description="Publication or site name.", max_length=100)
    tag: Optional[str] = Field(None, description="Short label like 'new' or 'security'. Omit if not applicable.", max_length=20)


class EmailSection(BaseModel):
    heading: str = Field(..., description="Section heading, e.g. 'MCP & Agent Tooling'. Max ~80 chars.", max_length=100)
    items: list[EmailItem] = Field(..., description="Items in this section.")


@mcp.tool()
def render_email(
    subject: str,
    sections: list[EmailSection],
    preheader: Optional[str] = None,
    intro: Optional[str] = None,
    footer_note: Optional[str] = None,
    template: str = "digest.html.j2",
) -> str:
    """
    Render a structured digest into a polished, ready-to-send HTML email.

    Call this AFTER gathering and summarizing search results, and BEFORE
    calling the email-send tool. Pass only plain-text fields -- do not
    include any HTML or markdown in subject, summaries, or headings.
    The template handles all styling and layout automatically.

    IMPORTANT: Keep all string values short and simple:
    - title: one short headline (< 150 chars)
    - summary: 1-3 plain sentences you wrote yourself (< 300 chars each)
    - Do NOT paste raw article text or URLs into summary/intro fields
    - Do NOT use double-quote characters inside any field value

    Returns a short message with the path to the saved HTML file.
    Pass that path as html_body_file to send_email — do NOT pass html_body.
    """
    if not sections or all(len(s.items) == 0 for s in sections):
        raise ValueError(
            "No items to render. Do not call render_email with empty sections -- "
            "if the search step found nothing, send a short plain-text 'no updates "
            "today' email instead of an empty digest."
        )

    available = {p.name for p in TEMPLATE_DIR.glob("*.j2")}
    if template not in available:
        raise ValueError(
            f"Unknown template '{template}'. Available templates: {sorted(available)}. "
            "Call list_email_templates to see options."
        )

    digest = {
        "subject": subject,
        "preheader": preheader or "",
        "intro": intro or "",
        "sections": [s.model_dump() for s in sections],
        "footer_note": footer_note or "",
    }
    _validate(digest)
    html = render_digest_email(digest, template_name=template)

    # Write to a temp file so the agent can pass the path to send_email
    # (html_body_file) instead of embedding raw HTML in JSON arguments.
    # Local LLMs cannot reliably JSON-encode multi-KB HTML strings.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", prefix="orchid_email_", delete=False, encoding="utf-8"
    ) as f:
        f.write(html)
        body_file = f.name

    return (
        f"Email rendered successfully ({len(html)} bytes).\n"
        f"HTML saved to: {body_file}\n"
        f"Pass html_body_file=\"{body_file}\" to send_email — do NOT pass html_body."
    )


@mcp.tool()
def list_email_templates() -> list[str]:
    """
    List available email template names that can be passed to render_email's
    `template` argument. Defaults to 'digest.html.j2' if not specified.
    """
    return sorted(p.name for p in TEMPLATE_DIR.glob("*.j2"))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
