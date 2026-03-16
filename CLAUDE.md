<!-- compressed 2026-03-16 -->

# CLAUDE.md — Orchid Framework

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
orchid --project <path> --mode auto|interactive [--code-model claude|local|auto] [--provider developer=ollama] [--offline]
orchid --check-providers
orchid --project <path> --status|--recall "q"|--search "q"|--add-task "t"
orchid --project <path> --tail | --inject "text"
orchid init <path> [--name --description --force]
orchid decide "Title" --decision "..." --rationale "..." --project <path>
orchid --multi --project <path-a> --project <path-b>
orchid telegram|slack|web --project <path> [--port 7842] [--host 0.0.0.0]
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
- **D0021** Process-per-project parallelism: isolated multiprocessing.Process. No shared mutable state.
- **D0022** Claude API rate limiting via multiprocessing.Semaphore. Local llama.cpp bypasses. Configurable via `multi.max_concurrent_claude_calls`.
- **D0023** Notification routing via multiprocessing.Queue; coordinator drains and calls callback. multi_formatter tags messages with `[project]` prefix.
- **D0024** Slack uses Socket Mode (slack-bolt + SocketModeHandler) — no public URL required.
- **D0025** Thread-per-task in Slack: task start posts new message, progress replies in thread.
- **D0026** BackgroundRunner shared between Telegram and Slack. Both are thin interface layers.
- **D0027** Web UI: FastAPI (REST + WebSocket) + React (Vite) at port 7842. Single process in production.
- **D0028** React frontend built with Vite to web_ui/dist/, served by FastAPI StaticFiles. Dev mode uses Vite proxy to :7842.
- **D0029** Traefik bare-metal file provider routes orchid.scheidy.com → localhost:7842, TLS via cloudflare certResolver.
- **D0030** ProviderBase ABC with 60s availability cache; ProviderRegistry singleton with 5-layer resolution: CLI flag → project config → ORCHID_\<AGENT\>_PROVIDER env → task_type default → agent_type hardcoded fallback.
- **D0031** All provider backends (Anthropic, LocalLlama, Ollama, OpenAI, OpenRouter, Bedrock) share ProviderBase. boto3 lazy import for Bedrock.
- **D0032** `--check-providers` probes all configured providers. `--offline` routes all to local. `--provider agent=provider` per-agent-type CLI override.

## Key Files
```
orchid/orchid.defaults.yaml
orchid/config.py / orchestrator.py / session.py / multi.py
orchid/agents/base.py / developer|researcher|reviewer.py / delegator.py
orchid/tools/models.py / session_log_parser.py
orchid/memory/state.py / decisions.py / vector.py
orchid/providers/__init__.py / base.py / registry.py / anthropic.py / local.py / ollama.py / openai.py / bedrock.py
orchid/interfaces/cli.py / telegram_bot.py / telegram_formatter.py / slack_bot.py / slack_formatter.py
orchid/interfaces/web_server.py / web_ui/ / multi_formatter.py / background_runner.py
scripts/orchid-{telegram,multi,slack,web}.service.template / traefik-orchid.yml
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
| T009–T032 | Done | Incl. chunking, log parser, dep tests, Slack formatter, Web UI, CLI --help, hello.py |
| Phase 3 M3.0–M3.4 | Done | deps/streaming/injection/notifications/routing/multi/Slack/Web UI/Provider registry |
| Tests | 208 passing | 188 + 20 new (test_providers.py) |

## Not Built
- SearXNG server setup (DDG fallback active)

## Recent
- **T032** Done: `orchid/hello.py` — `def hello(name: str = "World") -> str: return f"Hello, {name}!"`