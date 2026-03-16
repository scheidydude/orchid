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
orchid --project <path> --mode auto|interactive [--code-model claude|local|auto] [--provider developer=ollama] [--offline]
orchid --check-providers
orchid --project <path> --status|--recall "q"|--search "q"|--add-task "t"
orchid --project <path> --tail | --inject "text"
orchid init <path> [--name --description --force]
orchid decide "Title" --decision "..." --rationale "..." --project <path>
orchid --multi --project <path-a> --project <path-b>
orchid telegram|slack|web --project <path> [--port 7842] [--host 0.0.0.0]
orchid serve [--watch-dir ~/LocalAI] [--project <path>] [--port 7842] [--host 0.0.0.0]
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
- **D0033** Auto-discovery via watchdog inotify: ProjectDiscovery scans watch_dirs (depth 2) for .orchid.yaml, watches for created/deleted (debounced 2s). Excludes .venv/node_modules/.git/__pycache__. Explicit projects bypass .orchid.yaml requirement.
- **D0034** `orchid serve` — unified persistent entry point: web UI + auto-discovery + optional agent loops. systemd service at scripts/orchid-serve.service. watch_dirs configurable via CLI or serve.watch_dirs in config.
- **D0035** AgentManager: per-project agent loop threads with APScheduler BackgroundScheduler for cron-based auto runs. Per-project persistent config in .orchid.yaml `persistent:` section (enabled, auto_run, auto_run_schedule, auto_run_code_model).

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
orchid/discovery.py / agent_manager.py
scripts/orchid-{telegram,multi,slack,web}.service.template / orchid-serve.service / traefik-orchid.yml
scripts/install-orchid-serve.sh
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
| T034 | **INCOMPLETE** | `orchid task done` should not require TITLE when --id provided; hit max iterations |
| T009–T032 | Done | Incl. chunking, log parser, dep tests, Slack formatter, Web UI, CLI --help, hello.py |
| T035 | Done | AnthropicProvider.complete(): exponential backoff+jitter on 429, max 3 retries, up to 60s wait |
| T036 | Done | discovery.py: skip inotify watch for non-existent dirs; exclude .venv/node_modules/.git from watchdog Observer |
| Phase 3 M3.0–M3.5 | Done | deps/streaming/injection/notifications/routing/multi/Slack/Web UI/Provider registry/Auto-discovery/orchid serve/AgentManager |
| Tests | ~230 passing | + test_discovery.py |

## Not Built
- SearXNG server setup (DDG fallback active)
## Recent Completions

- [T037] Create scripts/deploy.sh — one-command deploy script that: 1) builds React frontend (npm run build in orchid/interfaces/web_ui/), 2) reinstalls orchid globally (uv tool install . --force), 3) restarts orchid-serve systemd service (sudo systemctl restart orchid-serve), 4) tails logs for 5 seconds to confirm clean startup. Add usage instructions as comments at top of script.: Created `orchid/scripts/deploy.sh` with the following features:

1. **Builds React frontend** - Runs `npm run build` in `orchid/interfaces/web_ui/`
2. **Reinstalls orchid globally** - Uses `uv tool in
