"""
render_email.py

Takes structured digest JSON (matching schema.json) and renders it into
a polished HTML email using a fixed Jinja2 template. The LLM never
touches markup -- it only ever produces the JSON payload.

Usage:
    from render_email import render_digest_email
    html = render_digest_email(digest_dict)

Or from CLI:
    python render_email.py input.json output.html
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_digest_email(digest: dict, template_name: str = "digest.html.j2") -> str:
    """
    Render a digest dict (matching schema.json) into HTML.
    Raises ValueError if required fields are missing.
    """
    _validate(digest)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template(template_name)

    return template.render(
        subject=digest["subject"],
        preheader=digest.get("preheader", ""),
        intro=digest.get("intro", ""),
        sections=digest["sections"],
        footer_note=digest.get("footer_note", ""),
        generated_at=datetime.now().strftime("%b %d, %Y %I:%M %p"),
    )


def _validate(digest: dict) -> None:
    if "subject" not in digest:
        raise ValueError("digest missing required field: subject")
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
