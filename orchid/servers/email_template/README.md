# email-render MCP server

Renders structured agent output into consistent, polished HTML email. The
agent never writes HTML — it produces plain-text fields, and a fixed template
turns them into the email. Same input, same look, every run.

## Why this exists

Letting an LLM freehand HTML produces inconsistent results — sometimes a styled
email, sometimes a plain text list. This splits the job cleanly:

- **The agent's job**: gather content, summarize → output structured fields.
- **This server's job**: take those fields, render fixed professional HTML.

## How it fits together

```
content source  →  agent curates  →  render_* tool   →  email MCP
  (search,           (LLM picks,       (this server:      (send the
   calendar,          writes plain      JSON → HTML,        HTML body)
   etc.)              text fields)      same look
                                        every time)
```

One Orchid task, one prompt, three tools called in sequence:
1. **content** — fetch or search for source material
2. **render_\*** (this server) — turn curated content into HTML
3. **email send** — pass the returned HTML string as the message body

See `AGENT_PROMPT.md` for an example task prompt.

## Tools

The server exposes one typed tool per template — the agent picks based on
content shape, getting the full schema from MCP tool introspection with no
pre-knowledge required.

### HTML render tools

| Tool | Template | Use when |
|---|---|---|
| `render_digest_email` | `digest.html.j2` | Multi-section cards: news roundups, curated link lists |
| `render_prose_email` | `prose.html.j2` | Flowing paragraphs: announcements, narrative updates |
| `render_list_email` | `list.html.j2` | Flat bullet/numbered list: recommendations, action items |
| `render_alert_email` | `alert.html.j2` | Single urgent message: incidents, threshold breaches |
| `render_recap_email` | `recap.html.j2` | Periodic summary: metric cards, highlights, detail sections |

Every HTML render tool returns a complete HTML string. Pass it directly to the
email-send tool as the body — nothing in between should touch it.

### Plain-text companions

Each HTML tool has a plain-text counterpart for multipart MIME email. Pass
both the HTML and plain-text bodies to your send tool so clients that strip
HTML (or prefer plain text) still get a readable message.

| Tool | Companion to |
|---|---|
| `render_digest_plaintext` | `render_digest_email` |
| `render_prose_plaintext` | `render_prose_email` |
| `render_list_plaintext` | `render_list_email` |
| `render_alert_plaintext` | `render_alert_email` |
| `render_recap_plaintext` | `render_recap_email` |

Plain-text tools accept the same fields as their HTML counterparts, minus
`preheader` and `logo_url` which are HTML-only.

### Utility tools

| Tool | Purpose |
|---|---|
| `preview_email` | Validate data and return a compact summary before rendering — subject char count, item counts, CTA presence, validation status. Call before render + send. |
| `get_template_schema` | Return the full field reference for a template (required/optional fields, types, descriptions). |
| `sanitize_subject` | Strip emoji from a subject line and report what was removed. |
| `list_email_templates` | List installed `.j2` files. Informational. |

## Shared optional parameters

All HTML render tools accept these optional parameters in addition to their
template-specific required fields:

| Parameter | Type | Description |
|---|---|---|
| `preheader` | string | Hidden text shown in inbox preview snippets. |
| `intro` | string | One-line framing shown below the header bar. |
| `footer_note` | string | Closing line in the footer. |
| `logo_url` | string | Publicly hosted logo image URL. Renders above the subject in the header bar. Use a light/white logo — the header background is dark. |
| `logo_alt` | string | Alt text for the logo (recommended for accessibility). |
| `unsubscribe_url` | string | Renders as an Unsubscribe link in the footer. |
| `strip_subject_emoji` | bool | Strip emoji from the subject before rendering. Default `false`. |

Plain-text tools accept `unsubscribe_url` (renders as `Unsubscribe: <url>` in the footer) but not `preheader`, `logo_url`, or `logo_alt`.

## Data contracts

### `render_digest_email`

```json
{
  "subject": "AI Infra Digest — June 20, 2026",
  "preheader": "3 items on MCP, 1 on Bedrock pricing",
  "intro": "Optional one-line framing.",
  "sections": [
    {
      "heading": "MCP & Agent Tooling",
      "items": [
        {
          "title": "Short headline",
          "summary": "1-3 plain sentences. No markdown.",
          "url": "https://source.com",
          "source": "Hacker News",
          "tag": "new"
        }
      ]
    }
  ],
  "footer_note": "Optional closing line.",
  "logo_url": "https://cdn.example.com/logo-white.png",
  "unsubscribe_url": "https://example.com/unsubscribe"
}
```

### `render_prose_email`

```json
{
  "subject": "June release notes",
  "intro": "Optional bold lede shown above the body.",
  "paragraphs": [
    "First paragraph text.",
    "Second paragraph text."
  ],
  "cta_text": "Read the full changelog",
  "cta_url": "https://example.com/changelog"
}
```

CTA button only renders if both `cta_text` and `cta_url` are provided.

### `render_list_email`

```json
{
  "subject": "5 tools worth trying this week",
  "list_style": "numbered",
  "items": [
    {
      "name": "Tool or item name",
      "description": "1-3 plain sentences.",
      "url": "https://example.com",
      "badge": "recommended",
      "meta": ["Author: Jane Smith", "Released June 2026"]
    }
  ]
}
```

`list_style` is `"bullet"` (default) or `"numbered"`. Per item: `url`,
`badge`, and `meta` are optional. `meta` items render as small
middot-separated facts below the description.

### `render_alert_email`

```json
{
  "subject": "Database cluster degraded",
  "alert_level": "error",
  "headline": "Primary replica failing health checks",
  "body": "The us-east-1 primary has failed 3 consecutive health checks. Automatic failover is in progress.",
  "details": ["Region: us-east-1", "Duration: 4m 32s", "Affected: 12% of reads"],
  "cta_text": "View runbook",
  "cta_url": "https://example.com/runbook"
}
```

`alert_level` controls the colour scheme: `"error"` (red), `"warning"` (amber),
`"info"` (blue), `"success"` (green). `details` and `cta_*` are optional.

### `render_recap_email`

```json
{
  "subject": "Weekly Recap — June 15–21, 2026",
  "period": "Week of June 15–21, 2026",
  "metrics": [
    {
      "label": "Total Searches",
      "value": "1,234",
      "delta": "+12%",
      "delta_sentiment": "positive"
    },
    {
      "label": "Error Rate",
      "value": "0.4%",
      "delta": "+0.1%",
      "delta_sentiment": "negative"
    }
  ],
  "highlights": [
    { "text": "Shipped dark mode support across all templates.", "tag": "shipped" },
    { "text": "Fixed SSRF vulnerability in the SearXNG server.", "tag": "fixed" }
  ],
  "sections": [
    {
      "heading": "Infrastructure",
      "items": ["Upgraded Python runtime to 3.12", "Added RENDER_ONLY CI mode"]
    }
  ]
}
```

`metrics`: 1–4 cards displayed prominently at the top. `delta_sentiment` is
`"positive"` (green ▲), `"negative"` (red ▼), or `"neutral"` (gray —).
`highlights`, `sections`, and `period` are optional.

## Files

| File | Purpose |
|---|---|
| `server.py` | MCP server. Registers all tools via FastMCP. |
| `render_email.py` | Rendering logic (Jinja2). One function per template type. CLI-runnable for digest. |
| `templates/digest.html.j2` | Multi-section card layout. |
| `templates/prose.html.j2` | Flowing paragraphs with optional CTA button. |
| `templates/list.html.j2` | Bullet or numbered list with badge and meta support. |
| `templates/alert.html.j2` | Single urgent alert with level-driven colour scheme. |
| `templates/recap.html.j2` | Periodic recap with metric cards and highlights. |
| `schema.json` | Example digest payload for reference. |
| `AGENT_PROMPT.md` | Sample Orchid task prompt for a daily digest workflow. |

## Setup

```bash
pip install -r requirements.txt
```

Register as a stdio MCP server alongside your search and email servers:

```json
{
  "mcpServers": {
    "email-render": {
      "command": "python",
      "args": ["/path/to/email-template/server.py"]
    }
  }
}
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `RENDER_ONLY` | `false` | Set to `1` or `true` to write rendered HTML to disk instead of returning it. Prevents accidental sends in CI pipelines. |
| `RENDER_OUTPUT` | `html` | Default for every HTML tool's `output` arg: `html` returns the markup, `file` writes a temp file and returns its path (pass as `html_body_file`). |
| `RENDER_ONLY_DIR` | `/tmp/email-render-preview/` | Output directory for `RENDER_ONLY` mode. |

## Testing without an agent

```bash
# Render the example digest directly
python render_email.py schema.json output.html
open output.html
```

For other templates, call render functions from Python directly:

```python
from render_email import render_prose_email, render_alert_email, render_recap_email

html = render_recap_email({
    "subject": "Weekly Recap",
    "metrics": [{"label": "Searches", "value": "1,234", "delta": "+12%", "delta_sentiment": "positive"}],
    "highlights": [{"text": "Shipped dark mode.", "tag": "shipped"}],
})
open("output.html", "w").write(html)
```

Or use `RENDER_ONLY` mode to write all renders to disk during development:

```bash
RENDER_ONLY=true python -c "
from render_email import render_digest_email
render_digest_email({'subject': 'Test', 'sections': [...]})
"
# HTML written to /tmp/email-render-preview/
```

## Editing templates

All templates use table-based HTML with inline styles — required for
Gmail/Outlook compatibility (`<style>` blocks, flexbox, and grid get stripped
or mangled by mail clients). Dark mode overrides are applied via
`@media (prefers-color-scheme: dark)` with CSS classes — clients that don't
support media queries (Gmail, desktop Outlook) fall back to the inline styles.

Test any template change by rendering locally and opening the output in a
browser before deploying.

To add a new template type:
1. Add a `.j2` file to `templates/`
2. Add a render function and validator to `render_email.py`
3. Add a typed tool to `server.py`
4. Add a schema entry to `_TEMPLATE_SCHEMAS` in `render_email.py`

The agent discovers the new tool through MCP introspection — no other changes
needed.

## Guardrails

All render tools validate input before rendering and raise `ValueError` with
an actionable message on failure:

- Subject lines longer than 78 characters are rejected (most clients
  hard-truncate beyond this). Subjects over 60 characters trigger a soft
  warning in `preview_email` output.
- Empty content is rejected with a message telling the agent what to do
  instead (e.g. send a plain "no updates today" email rather than an empty
  digest).
- Invalid enum values (`alert_level`, `list_style`, `delta_sentiment`) are
  rejected with the list of valid options.
- All required fields are validated so malformed agent output fails loudly
  rather than producing a broken email.
