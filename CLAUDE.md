<!-- compressed 2026-03-16 -->

# CLAUDE.md — Orchid Framework (compressed)

## What It Is
Standalone AI agent orchestration tool. Installed globally, invoked against external project dirs. Projects opt in via CLAUDE.md + tasks.md + optional .orchid.yaml.

## Layout
```
~/orchid/          ← tool repo
~/projects/webtron/
  CLAUDE.md / tasks.md / .orchid.yaml
  .orchid/decisions.json, session_logs/, chroma/
```

## Key CLI
```bash
orchid --project <path> --mode auto|interactive [--code-model claude|local|auto]
orchid --project <path> --status|--recall "q"|--search "q"|--add-task "t"
orchid --project <path> --tail | --inject "text"
orchid init <path> [--name --description --force]
orchid decide "Title" --decision "..." --rationale "..." --project <path>
orchid --multi --project <path-a> --project <path-b> [--code-model auto]
orchid telegram --project <path> [--multi ...]
orchid slack --project <path>   # tokens: SLACK_BOT_TOKEN, SLACK_APP_TOKEN
orchid web --project <path> [--port 7842] [--host 0.0.0.0] [--dev]
```
tasks.md: `- [ ] **T003** Title \`type:code_generate\` \`p1\` \`needs:T001,T002\` \`model:claude\``

## Architecture Decisions
- **D0001** File-based state (tasks.md, CLAUDE.md, decisions.json, session_logs). No DB.
- **D0002** Two-tier routing: Claude API → orchestrate/review/plan/critique/synthesize; llama.cpp → draft/code_generate/summarize/search.
- **D0003** ReAct loop (Reason→Act→Observe), text-parsed. No function-calling API.
- **D0004** Interface-agnostic core; orchid/interfaces/ is thin layer.
- **D0005** Three-layer config merge: orchid.defaults.yaml → .orchid.yaml → CLI flags.
- **D0006** Standalone runtime. Projects NOT subfolders of Orchid.
- **D0007** ChromaDB embedded at `<project>/.orchid/chroma/`. No server.
- **D0008** Embedding priority: llama.cpp `/v1/embeddings` (LLAMA_EMBED_URL, port 8081, nomic-embed-text) → sentence-transformers all-MiniLM-L6-v2. Never OpenAI.
- **D0009** Auto-embed session log on close; auto-recall top-k into `## Recalled Context` on load.
- **D0010** Search priority: SearXNG → Brave → DuckDuckGo. Auto-probes and caches.
- **D0011** Content extraction: trafilatura → BeautifulSoup. Truncated to `web_search.max_page_chars` (default 8000).
- **D0012** Agent delegation via ReAct action `delegate[agent_type | task]`. Depth-limited to `delegation.max_depth` (default 3). Sub-agents: `max_sub_iterations=5`.
- **D0013** Sub-context: task + top-3 vector recall + 1000-char parent context slice. Never full parent ReAct trace.
- **D0014** Telegram: TelegramBot + BackgroundRunner + telegram_formatter. No business logic in bot.
- **D0015** User whitelist: TELEGRAM_ALLOWED_USERS (comma-separated). Unset = warn + accept all.
- **D0016** Multi-tier model routing: CLI flag → task `model:` → keyword heuristic → type default. Returns RouteDecision(model, reason, source).
- **D0017** Task dependencies via `needs:T001,T002`. Cycle detection warns+skips. Controlled by `dependencies.enabled`.
- **D0018** Live streaming log: `.live.log` → renamed `.log` on close. `--tail` tails it.
- **D0019** Mid-run injection via `.orchid/inject.queue`. Agent reads+clears each ReAct iteration.
- **D0020** Proactive Telegram notifications at session_start/task_start/task_complete/task_failed/session_complete. Configurable via `telegram.notify_on`.
- **D0021** Process-per-project parallelism: isolated multiprocessing.Process per project. No shared mutable state.
- **D0022** Claude API rate limiting via multiprocessing.Semaphore. Local llama.cpp bypasses. Configurable via `multi.max_concurrent_claude_calls`.
- **D0023** Notification routing via multiprocessing.Queue; coordinator drains and calls callback. multi_formatter tags messages with `[project]` prefix.
- **D0024** Slack uses Socket Mode (slack-bolt + SocketModeHandler) — no public URL or ngrok required. Fits homelab/bare-metal deployment.
- **D0025** Thread-per-task in Slack: task start posts new message, all progress replies in that thread. Reduces channel noise.
- **D0026** BackgroundRunner is shared between Telegram and Slack bots. Both are thin interface layers — no business logic in either bot.
- **D0027** Web UI: FastAPI (REST + WebSocket) + React (Vite) served together at port 7842. Single process in production.
- **D0028** React frontend built with Vite to web_ui/dist/, served by FastAPI StaticFiles. Dev mode uses Vite proxy to :7842.
- **D0029** Traefik bare-metal file provider routes orchid.scheidy.com → localhost:7842 with TLS via cloudflare certResolver.

## Key Files
```
orchid/orchid.defaults.yaml
orchid/config.py / orchestrator.py / session.py / multi.py
orchid/agents/base.py / developer|researcher|reviewer.py / delegator.py
orchid/tools/models.py / session_log_parser.py
orchid/memory/state.py / decisions.py / vector.py
orchid/interfaces/cli.py / telegram_bot.py / telegram_formatter.py
orchid/interfaces/slack_bot.py / slack_formatter.py
orchid/interfaces/web_server.py / web_ui/ (React + Vite frontend)
orchid/interfaces/multi_formatter.py / background_runner.py
scripts/orchid-telegram.service.template / orchid-multi.service.template
scripts/orchid-slack.service.template / orchid-web.service.template
scripts/traefik-orchid.yml
```

## Install
```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env  # ANTHROPIC_API_KEY required; llama.cpp: localhost:8080
```

## Task Status
| ID | Status | Notes |
|----|--------|-------|
| T007 | **INCOMPLETE** | DDG ad-result filter (y.js URLs) |
| T008 | **INCOMPLETE** | decisions.json JSON Lines parse error |
| T009–T026 | Done | Incl. chunking fix, log parser, dependency tests |
| Phase 3 M3.0 | Done | deps/streaming/injection/notifications/routing |
| Phase 3 M3.1 | Done | multi.py, multi_formatter.py, CLI --multi, Telegram --multi |
| Phase 3 M3.2 | Done | slack_bot.py, slack_formatter.py, CLI slack, Socket Mode |
| Phase 3 M3.3 | Done | web_server.py, React UI, CLI web, Traefik config |
| Tests | 188 passing | 170 + 18 new (test_web.py) |

## Not Built
- SearXNG server setup (DDG fallback active)
## Recent Completions

- [T027] test task from Slack: 

- [T028] Fix Slack formatter: hot memory code blocks missing closing triple backtick in Slack messages: 

- [T029] Test Web UI live task creation: 

- [T030] Test CLI --help option: The CLI --help option works correctly. It displays comprehensive usage information including:

**Main Options:**
- `--project/-p`: Path to project directory (repeatable for multi-project mode)
- `--mo
