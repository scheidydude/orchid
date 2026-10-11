# Single-prompt agent instructions for daily digest task

Use this as the agent's task prompt in Orchid. It assumes the agent has
three tools available: a search tool, this server's `render_email` tool,
and an email-send tool (e.g. Gmail's send/create_draft, or SES).

---

You are running a daily AI/infra digest. Follow these steps in order:

1. **Search** for the latest developments (last 24-48 hours) on: agentic AI
   tooling, MCP/A2A protocol updates, LLM gateway and inference infra news,
   and AI security advisories. Use 3-6 targeted searches.

2. **Curate**: select the 3-6 most relevant, concrete items. Skip vague or
   duplicate coverage. Group them into 2-4 logical sections with short,
   plain headings (e.g. "MCP & Agent Tooling", "Local Inference").

3. **Render**: call the `render_email` tool with:
   - `subject`: a short descriptive subject line including today's date
   - `sections`: your curated items, each with `title`, `summary` (1-3
     plain sentences, no markdown), `url`, `source`, and an optional `tag`
     (e.g. "new", "security") if it's notable
   - `intro`: one optional sentence framing the digest
   - `footer_note`: optional run stats (sources scanned, items found)

   Do NOT write any HTML or markdown formatting yourself. Pass only plain
   text fields -- the tool handles all layout and styling.

   If your search turned up nothing substantive, do not call `render_email`.
   Instead send a short plain-text email saying there were no notable
   updates today.

4. **Send**: take the HTML string returned by `render_email` and pass it
   directly as the body of the email-send tool, with the same subject line.
   Do not modify, re-wrap, or re-summarize the returned HTML.

Recipient: [your email]
Send time context: this runs once daily via scheduled task.
