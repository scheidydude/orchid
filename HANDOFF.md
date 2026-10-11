# HANDOFF.md
_Written 2026-10-10 21:45 PDT. Previous handoff archived as `HANDOFF-archive-2026-10-10-2144.md`. Ground truth: orchid `main` at `7fb632d` (pushed), mcp-servers `main` at `77b7ddb` (pushed)._

---

## 1. Mission

Make the scheduled email digests (stored as per-user scheduled tasks in `~/.config/orchid/users.json`, executed by the cron engine inside `orchid-serve.service`) reliable and good-looking. The "Orchid email at 9am" job (`stask_a6c399f0`, Top 10 Trending LLM Repos) was the reference; the other digests were falling back to plain text or returning empty results. The session fixed the email-template server, the cron engine, the MCP HTTP client, and the task prompts/config.

---

## 2. Current State

**Verified working (checked 2026-10-10 ~21:40 PDT after the service restart at 21:35:55):**
- `stask_a6c399f0` (9am Mon-Fri) and `stask_68bcdad9` (7am daily) both ran `success` after the restart: GitHub search via the hosted endpoint returned 10 repos with star counts (e.g. affaan-m/ECC 276,606), `render_list_email` produced an HTML file, `send_email` (smtp via Resend SMTP) was called. Source: `~/.config/orchid/cron/runs.jsonl`.
- Installed tool copy (`~/.local/share/uv/tools/orchid/.../site-packages/orchid`) contains the HTTP client fix, date placeholders, weekday fix and file-output mode (grep-verified; reinstalled with `--reinstall --refresh-package orchid`).
- Full test suite was green at `5796b9f`: 1898 passed, 1 skipped (~2 min). Later commits touched only `cron/engine.py`, `mcp/http_client.py`, `cron/executor.py` and their tests; those test files pass (cron executor 24, cron engine 17, mcp-related 122) but the **full suite was not re-run after `5046698`**.

**Unverified:**
- Whether the delivered emails actually *render as styled HTML* in the inbox. The agent's summaries say "HTML digest attached" / "HTML version at /tmp/email_list_*.html" which is ambiguous. Open the actual email from the 21:36 runs and check.
- `stask_d6ba7c13` (8am AI/infra digest) has not run since being switched to `smtp`/`render_digest_email`/`{today_long}`.
- `{today_long}` in subjects has only been verified in an in-process run (subject came out "October 10, 2026"), not yet in a real scheduled run's received email.
- The weekday fix (`mon-fri`) has not yet been seen across a weekend. Next real check: Mon 2026-10-12 09:00 PDT should run; Sat/Sun should not.

**Not done:**
- News tasks `stask_b1046aa5` (Scheidydude) and `stask_75212529` (Jen Pitts) still use `resend-email` and model-written plain text, no template. The Jen Pitts task is named "...copy" with a wrong description.
- Executor does not log tool-call args/results (see Gotchas); `last_run_at` / `last_run_status` on tasks are never written back (always null).
- Uncommitted: `.claude/settings.local.json` (tracked file) gained a permission rule for the scratchpad script `apply_email_changes.py`. Harmless; decide whether to commit or revert. `temp/` is untracked and pre-existing; ignore.

**Exact next action:** open the 7am/9am emails from today and confirm HTML renders; then see Open Questions Q1-Q3.

---

## 3. Decisions Made (and Why)

1. **File output mode for email render tools, backported upstream first.**
   - Decision: `output: "html" | "file"` on all HTML render tools in `mcp-servers/email-template`, default via `RENDER_OUTPUT` env; orchid's copy defaults it to `file`.
   - Alternatives: keep orchid's fork; return HTML inline.
   - Reason: local LLMs can't reliably JSON-encode multi-KB HTML into a send-tool call (caused "invalid JSON in tool-call arguments"). One source of truth beats a diverging fork.
   - Reversibility: easy (env var / arg).

2. **Orchid's `email_template` is upstream + 3 additions** (setdefault `RENDER_OUTPUT=file`, deprecated `render_email` alias for `render_digest_email`, `main()` entry point). Rest is a straight copy; README has an "Orchid note". Re-sync means re-applying those three. Alias is only a safety net; nothing referenced `render_email`.

3. **Send via `orchid-mcp-smtp` pointed at Resend SMTP, not `resend-email`.**
   - Reason: `resend-mcp` 2.6.0 `send-email` accepts only inline `html` (+ attachments), never an HTML file path, so file mode fell back to plain text. `smtp.py` supports `html_body_file`.
   - Config: `smtp.resend.com`, port 465, `SMTP_USE_SSL=true`, user `resend`, password `${RESEND_API_KEY}`, `SMTP_FROM=orchid@mail.wherestheplot.com` in `~/.config/orchid/config.yaml`. Auth verified (login only) before applying.
   - Alternative rejected: `RENDER_OUTPUT=html` + resend (model must copy the HTML into args; fragile).
   - Reversibility: easy (backup dirs below).

4. **GitHub MCP: hosted endpoint `https://api.githubcopilot.com/mcp/`** (transport http, `Authorization: Bearer ${GITHUB_TOKEN}`) instead of `@modelcontextprotocol/server-github` (2025.4.8) or the Docker image `ghcr.io/github/github-mcp-server`.
   - Reason: old server's `search_repositories` takes only `query/page/perPage` and returns no `stargazers_count`. Hosted and Docker both support `sort` (`stars|forks|help-wanted-issues|updated`), `order`, and return stars in the default small response (`minimal_output=false` is 8x larger, no benefit). Hosted avoids a container start per run and image pulls.
   - Reservation: PAT is sent on every call over HTTPS; keep it read-only/public-scoped. Docker image was pulled locally and works as a fallback.
   - Reversibility: easy (config.yaml block; backups).

5. **Fixed `HTTPMCPClient` for MCP Streamable HTTP** rather than shipping a different client: SSE response parsing, `Mcp-Session-Id` echo + DELETE on disconnect, id-less notifications, 202/empty OK, `Accept: application/json, text/event-stream`, URL no longer `rstrip("/")`, protocol 2025-03-26. Existing 3 tests still pass; 4 added.

6. **Cron weekday fix in the engine, not just in task JSON.** Standard cron `1-5` ran Tue-Sat because APScheduler numbers Monday=0. `_cron_dow_to_names()` expands numeric DOW to day names (handles lists, ranges, `*/n`, 0/7=Sun). Task `a6c399f0` also uses `mon-fri` in its schedule string, which works on old and new engines.

7. **Date placeholders** (`{today}`, `{today_long}`, `{today±Nd|Nw}`) expanded in `agent_prompt`/`agent_tool` prompt and system at run time (`expand_date_placeholders` in `cron/executor.py`, local time). Reason: the local model has no clock and invents dates (it wrote "2024-11-15" and "January 20, 2026"). Alternative considered: always append "today is X" to the system prompt (not done; would affect all tasks). Unknown `{...}` is left untouched.

8. **Prompts dropped date arithmetic and recency filters.** Queries are `topic:llm stars:>5000` and `topic:ai-assisted-development stars:>100` with `sort stars, order desc, perPage 10`; badge = `'<stargazers_count> stars'`. Reason: a `pushed:>YYYY-MM-DD` filter the model had to compute caused an empty search ("No notable trending LLM repositories"). Tradeoff: "trending" now means "highest-starred", not recently active.

9. **Orchestrator MCP tool-injection bugfix** (`orchestrator.py`): it read non-existent `MCPTool.server_name`; now iterates `list_tools_by_server()`. This failed any task run with a connected MCP server.

10. **Tests isolate local `.env`**: autouse fixture in `tests/conftest.py` sets `ORCHID_{ORCHESTRATOR,REVIEWER,DEVELOPER,RESEARCHER}_PROVIDER` and `ORCHID_OFFLINE_MODE` to `""` (empty, not deleted, so subprocess tests that call `load_dotenv` don't repopulate them).

---

## 4. Architecture & Key Files

Created/synced this session:
- `orchid/servers/email_template/{server.py,render_email.py,templates/*.j2,README.md,AGENT_PROMPT.md,schema.json,sample_output.html}` - synced from `~/LocalAI/mcp-servers/email-template` (15 tools: `render_{digest,prose,list,alert,recap}_email`, `*_plaintext`, `preview_email`, `get_template_schema`, `sanitize_subject`, `list_email_templates`, plus alias `render_email`).
- `HANDOFF-archive-2026-10-10-2144.md` - previous handoff (CLI auth work, May 2026).

Modified significantly (conceptually):
- `orchid/cron/engine.py` - `_cron_dow_to_names()`, used in `_parse_cron_expression`.
- `orchid/cron/executor.py` - `expand_date_placeholders()`, applied in `_run_agent_prompt` / `_run_agent_tool`.
- `orchid/mcp/http_client.py` - Streamable HTTP.
- `orchid/orchestrator.py` (~line 560) - MCP tool injection via `list_tools_by_server()`.
- `tests/conftest.py`, `tests/test_cron_engine.py`, `tests/test_cron_executor.py`, `tests/test_mcp_http_client.py`.

Outside the repo (live config, not version-controlled):
- `~/.config/orchid/config.yaml` - `mcp_servers.github` (hosted http), `mcp_servers.smtp` (Resend SMTP), `resend-email` unchanged.
- `~/.config/orchid/users.json` - `scheduled_tasks` for user `admin` (see task table below).
- `~/.config/orchid/mcp_catalog.json` - **unchanged**; still has stdio `@modelcontextprotocol/server-github`.
- `~/.config/orchid/.env` (RESEND_API_KEY, GITHUB_TOKEN...) and repo `.env` (gitignored; sets `ORCHID_*_PROVIDER=local`).
- `~/.config/orchid/backups/20261010-204343-pre-smtp-engine-fix/`, `...212336-pre-hosted-github/`, `...213041-pre-date-placeholders/` - rollback copies (users.json, config.yaml, plus per-task JSON in the first two). Each restores by copying files back and restarting.

Template-using tasks (all `agent_tool`, run by local `qwen3.5-122b-a10b-q4` at `localhost:8082`, local time = PDT):

| Task | Cron | Servers |
|---|---|---|
| `a6c399f0` Top 10 Trending LLM Repos | `0 9 * * mon-fri` | github, email-template, smtp |
| `68bcdad9` AI-Assisted-Dev digest | `0 7 * * *` | github, email-template, smtp |
| `d6ba7c13` Daily AI/infra digest | `0 8 * * *` | searxng, email-template, smtp |

Files that look touchable but shouldn't be: see section 8.

---

## 5. Gotchas & Hard-Won Knowledge

- **`uv tool install --force .` can keep stale code.** The service runs a non-editable copy built from this repo with version `3.1.44` unchanged, so uv reuses its cached wheel. Deploy with `cd ~/LocalAI/orchid && uv tool install --force --reinstall --refresh-package orchid .`, then `sudo systemctl restart orchid-serve`. Symptom of a stale deploy: hosted GitHub config + old HTTP client = `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` on job start (this happened once).
- **The service holds `users.json` in memory and rewrites the whole file on its next save.** Direct edits to the file can be overwritten and jobs run the task dict captured at registration. Safe order: `sudo systemctl stop orchid-serve` -> edit -> reinstall if code changed -> `sudo systemctl start orchid-serve`. The assistant has no sudo.
- **Runs record `success` even when the output is an error string.** Tool/arg errors are returned to the model as `"Error: ..."`, and `openai.py` returns `"Error: model generated invalid JSON in tool-call arguments..."` as the final text. Always read `runs.jsonl` output, not just status.
- **Executor logs no tool-call args/results** (`_run_agent_tool` -> `provider.complete_with_tools`). To debug, run the task in-process with a wrapper around `LocalProvider.complete_with_tools` that wraps `dispatch_fn` (do it with `servers` minus `smtp` so nothing is sent). `load_dotenv(~/.config/orchid/.env)` first.
- **Local model has no clock and weak at date math/JSON.** Use placeholders, keep field values short, no double quotes in values, no HTML/markdown in args.
- **Repo `.env` leaks into pytest.** `orchid.config` calls `load_dotenv()` on import; repo `.env` sets `ORCHID_*_PROVIDER=local`, which broke 18 tests locally (CI has no `.env`). Fixed by the conftest fixture. Also: that `.env` still sets those for the running service (intentional local routing).
- **`pytest-timeout` is not installed in `.venv`**; `--timeout` errors. Use shell `timeout`.
- **Config precedence:** repo `.env` is loaded before `~/.config/orchid/.env`, `override=False`, so the repo file wins on conflicts. `${VAR}` in `config.yaml` is expanded; catalog entries (`mcp_catalog.json`) are NOT env-expanded in the executor, and `config.yaml` entries are merged first, so a `config.yaml` server shadows a catalog server of the same id.
- **`resend-mcp` `send-email` cannot take an HTML file** as body (only `attachments[].filePath`). `smtp.py` requires `to`, `subject`, `body`; `html_body_file` is read from local disk, so the email-template MCP and smtp MCP must run on the same host (they do).
- **Cron timezone:** jobs fire in local time (PDT) although comments say UTC; `runs.jsonl` timestamps are UTC (9am PDT = 16:00 UTC).
- **GitHub old MCP server** returns no `stargazers_count` and only `query/page/perPage`; `sort:stars` inside the query text is harmless but redundant.
- Hosted GitHub endpoint replies as `text/event-stream` and issues `Mcp-Session-Id`; clients that call `resp.json()` directly fail.
- `git stash` during lint checks is a bad habit here (`temp/` and settings are dirty); use `git diff`/`git show` instead.

---

## 6. Conventions In Play

- Follow `CLAUDE.md` (v3.0 notes). `from __future__ import annotations` must NOT appear in `cron/api.py`; `cron/executor.py` and `cron/engine.py` are fine.
- Activate the venv (`source .venv/bin/activate`) before python/pytest/ruff. Ruff is enforced; `ruff check --fix` is the norm (commit history has ruff-only commits).
- Never hardcode model names or call the anthropic SDK directly (use providers/registry).
- Push after each phase; commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Live-config edits: take a timestamped backup under `~/.config/orchid/backups/`, apply via a guarded script (aborts if the live file differs from the backup hash), write `users.json` atomically with mode 0600. The one-off scripts were in the session scratchpad (`/tmp/claude-1000/.../scratchpad/apply_*.py`) and are not in the repo; recreate if needed.
- Prompt style for local-model tasks: numbered steps, explicit tool names (`render_list_email`, `send_email` with `html_body_file`), fallback "if nothing found send a plain-text no-updates email", `system` = "Use the available tools to complete the task. Never write HTML yourself."
- The assistant was running in "caveman" terse-reply mode (hook-driven); code, commits, and this file are written normally.

---

## 7. Open Questions

1. Do the received emails from 2026-10-10 21:36 (a6c399f0, 68bcdad9) render as styled HTML? (If plain: check `smtp.py` `_build_message`, whether the model passed `html_body_file`, and Resend SMTP behaviour.)
2. Should the two plain-text news tasks (`b1046aa5`, `75212529`) move to `servers: ["searxng","email-template","smtp"]` with `render_prose_email`/`render_digest_email`, and rename/fix the Jen Pitts task?
3. Add tool-call logging to `_run_agent_tool` (args + truncated results) and write `last_run_at`/`last_run_status` back to tasks?
4. Should every `agent_*` task get an automatic "Today is YYYY-MM-DD (weekday)" system line, in addition to placeholders?
5. Mark runs `failure` when the final output starts with `Error:`? (Currently `success`.)
6. Bump `pyproject.toml` version on each change so `uv tool install --force .` can't go stale?
7. Should `mcp_catalog.json`'s `github` entry be switched to the hosted endpoint too (needs vault `requires_credential` or env expansion), or left shadowed by `config.yaml`?
8. Commit or revert the local-only permission rule in `.claude/settings.local.json`?

---

## 8. Do Not Touch

- `~/.config/orchid/users.json` and `config.yaml` while `orchid-serve` is running (see Gotchas); never without a fresh backup.
- Disabled tasks `f24d4b90`, `ce176d92`, `938d0438`, `03512200` - left as-is on purpose; `ce176d92` calls `search_repositories` via `mcp_tool` with old-server args and has not been checked against the hosted schema.
- `mcp-servers/email-template` is the **upstream**; make email-template changes there first, then copy to `orchid/orchid/servers/email_template/` and re-apply the 3 orchid additions.
- `orchid/web/server.py` is a dead file (routes live in `orchid/interfaces/web_server.py`); `orchid/interfaces/portal/vite.config.js` `base: '/app/'` is load-bearing.
- The repo `.env` and `~/.config/orchid/.env` (secrets; never print values).
- `HANDOFF-archive-*.md` files.
- Settled decisions in section 3 (smtp-over-Resend-SMTP, hosted GitHub MCP, file output default, no date math in prompts). Revisit only with new evidence.

---

## 9. Resume Command

> "Read HANDOFF.md. Then open the 9am/7am digest emails from today and check they render as styled HTML (Open Question 1); if yes, move on to the two plain-text news tasks (Q2) and add tool-call logging to `_run_agent_tool` (Q3). Do not edit `~/.config/orchid/users.json` or `config.yaml` while `orchid-serve` is running, and do not switch the GitHub MCP server again. Confirm before making changes outside `orchid/cron/`, `orchid/mcp/`, `orchid/servers/email_template/` and the scheduled-task JSON."
