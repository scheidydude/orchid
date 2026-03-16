<!-- compressed 2026-03-15 -->

# CLAUDE.md — Orchid Framework

## What It Is
Standalone AI agent orchestration tool. Installed globally, invoked against external project dirs. Projects opt in via CLAUDE.md + tasks.md + optional .orchid.yaml.

## Layout
```
~/orchid/          ← tool (this repo)
~/projects/webtron/
  CLAUDE.md / tasks.md / .orchid.yaml
  .orchid/decisions.json, session_logs/, chroma/
```

## Usage
```bash
orchid --project <path> --mode auto|interactive
orchid --project <path> --mode auto --code-model claude|local|auto
orchid --project <path> --status|--recall "q"|--search "q"|--add-task "t"
orchid --project <path> --tail             # tail live agent log
orchid --project <path> --inject "text"    # inject context into running agent
orchid init <path> [--name --description --force]
orchid decide "Title" --decision "..." --rationale "..." --project <path>
orchid task add|done --project <path>
orchid telegram --project <path>   # token: TELEGRAM_BOT_TOKEN
```

tasks.md syntax:
```
- [ ] **T003** Task title `type:code_generate` `p1` `needs:T001,T002` `model:claude`
```

## Architecture Decisions
- **D0001** File-based state (tasks.md, CLAUDE.md, decisions.json, session_logs). No DB.
- **D0002** Two-tier routing: Claude API → orchestrate/review/plan/critique/synthesize; llama.cpp → draft/code_generate/summarize/search.
- **D0003** ReAct loop (Reason→Act→Observe), text-parsed. No function-calling API required.
- **D0004** Interface-agnostic core; orchid/interfaces/ is thin layer. CLI first; Telegram/Slack reserved.
- **D0005** Three-layer config merge: orchid.defaults.yaml → .orchid.yaml → CLI flags. `configure_for_project()` resets singleton.
- **D0006** Standalone runtime. Projects NOT subfolders of Orchid.
- **D0007** ChromaDB embedded at `<project>/.orchid/chroma/`. No server.
- **D0008** Embedding priority: llama.cpp `/v1/embeddings` (LLAMA_EMBED_URL, port 8081, nomic-embed-text) → sentence-transformers all-MiniLM-L6-v2. Never OpenAI.
- **D0009** Auto-embed session log on close; auto-recall top-k into `## Recalled Context` on load (if `vector_memory.auto_recall_on_load: true`).
- **D0010** Search priority: SearXNG (SEARXNG_URL) → Brave (BRAVE_API_KEY) → DuckDuckGo. Auto-probes and caches. Forceable via `web_search.backend`.
- **D0011** Content extraction: trafilatura → BeautifulSoup fallback. Truncated to `web_search.max_page_chars` (default 8000).
- **D0012** Agent delegation via ReAct action `delegate[agent_type | task]`. Depth-limited to `delegation.max_depth` (default 3). Sub-agents run with `max_sub_iterations=5`.
- **D0013** Sub-context slimming: passes task + top-3 vector recall + 1000-char parent context slice. Never full parent ReAct trace.
- **D0014** Telegram: thin layer — TelegramBot (command dispatch), BackgroundRunner (thread pool/asyncio bridge), telegram_formatter (plain-text). No business logic in bot.
- **D0015** User whitelist: TELEGRAM_ALLOWED_USERS (comma-separated IDs). Unset = warn + accept all (dev mode).
- **D0016** Multi-tier model routing: CLI flag → task `model:` annotation → keyword heuristic → type-based default. Returns RouteDecision(model, reason, source). Logged at each task start.
- **D0017** Task dependencies via `needs:T001,T002` annotation in tasks.md. `is_runnable(completed_ids)` gates task selection. Cycle detection warns and skips. Controlled by `dependencies.enabled`.
- **D0018** Live streaming log: session writes `.live.log` (human-readable ReAct iterations). Renamed to `.log` on close. CLI `--tail` tails it. Agents call `stream_callback` after each iteration.
- **D0019** Mid-run context injection via `.orchid/inject.queue` file. Agent reads+clears on each ReAct iteration. CLI `--inject`, Telegram `/inject`, BackgroundRunner.inject() all write to same file.
- **D0020** Proactive Telegram notifications: BackgroundRunner fires `notification_callback(event, data)` at session_start/task_start/task_complete/task_failed/session_complete. Configurable via `telegram.notify_on`.

## Key Files
```
orchid/orchid.defaults.yaml
orchid/config.py                  load_defaults, configure_for_project, merge_for_project
orchid/orchestrator.py            main loop, task routing, agent dispatch
orchid/session.py                 state lifecycle, context loading, compression
orchid/agents/base.py             ReAct loop, tool registry, SIGALRM
orchid/agents/developer|researcher|reviewer.py
orchid/agents/delegator.py        AgentDelegator — spawns sub-agents, depth-limits
orchid/tools/models.py            call/route/embed + httpx retry wrapper
orchid/memory/state.py            tasks.md+CLAUDE.md HTML-comment-aware parser
orchid/memory/decisions.py        JSON Lines append-only
orchid/memory/vector.py           ChromaDB embedded
orchid/interfaces/cli.py
orchid/interfaces/telegram_bot.py
orchid/interfaces/telegram_formatter.py
orchid/interfaces/background_runner.py
scripts/orchid-telegram.service.template
```

## Install
```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env  # ANTHROPIC_API_KEY required
# llama.cpp: http://localhost:8080/v1 (override: LLAMA_BASE_URL)
```

## Task Status
| ID | Status | Notes |
|----|--------|-------|
| T007 | **Incomplete** | DDG ad-result filter (y.js URLs) — needs finish |
| T008 | **Incomplete** | decisions.json JSON Lines parse error — needs fix |
| T009–T023 | Done | Phase 2 complete |
| Phase 3 M3.0 | **Done** | Fix 1–5 (deps, streaming, injection, notifications, routing) |
| All 130 tests | Passing | 84 original + 46 new |

## Not Built Yet
- Slack interface (reserved, same pattern as Telegram)
- Multi-project parallelism (--multi stub present, not wired)
- SearXNG server setup (DDG fallback active)
## Recent Completions

- [T022] Investigate and fix chunking producing oversized token payloads - chunks exceeding 1024 tokens despite chunk_size=400 word setting. Likely word-based chunking not accounting for tokenization overhead. Switch to token-based chunking with hard cap at 800 tokens.: 

- [T023] Archive all completed tasks to tasks.md archive section now: I've archived all completed tasks to tasks.md. The file now has:

- **TODO** section with the current archiving task (T023)
- **ARCHIVED** section with all 22 completed tasks organized under the 2026-

- [T024] Write a complex regex parser for extracting structured data from session logs: Thought: Let me use base64 encoding to avoid JSON escaping issues.

Action: bash
Action Input: {"command": "base64 -d << 'B64END' > orchid/tools/session_log_parser.py\nIiIiQ29tcGxleCByZWdleCBwYXJzZXIg

- [T025] Dependency test parent task: 

- [T026] Dependency test child task: Task T026 "Dependency test child task" is already complete. It was successfully marked as done in tasks.md. This task was part of a dependency test where:

- **T025** (parent task) was completed first
