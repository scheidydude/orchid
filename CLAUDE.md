<!-- compressed 2026-03-15 -->

# CLAUDE.md — Orchid Framework (Compressed)

## What It Is
Orchid: standalone AI agent orchestration tool installed once globally, invoked against external project dirs. Projects opt in via CLAUDE.md + tasks.md + optional .orchid.yaml.

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
orchid --project <path> --status|--recall "q"|--search "q"|--add-task "t"
orchid init <path> [--name --description --force]
orchid decide "Title" --decision "..." --rationale "..." --project <path>
orchid task add|done --project <path>
```

## Architecture Decisions
- **D0001** File-based state (tasks.md, CLAUDE.md, decisions.json, session_logs). No DB.
- **D0002** Two-tier routing: Claude API → orchestrate/review/plan/critique/synthesize; llama.cpp → draft/code_generate/summarize/search.
- **D0003** ReAct loop (Reason→Act→Observe), text-parsed. No function-calling API required.
- **D0004** Interface-agnostic core; orchid/interfaces/ is thin layer. CLI first; Telegram/Slack reserved.
- **D0005** Three-layer config merge: orchid.defaults.yaml → .orchid.yaml → CLI flags. `configure_for_project()` resets singleton.
- **D0006** Option B standalone runtime. Projects NOT subfolders of Orchid.
- **D0007** ChromaDB embedded mode at `<project>/.orchid/chroma/`. No server.
- **D0008** Embedding priority: llama.cpp `/v1/embeddings` (LLAMA_EMBED_URL, port 8081, nomic-embed-text) → sentence-transformers all-MiniLM-L6-v2. Never OpenAI.
- **D0009** Auto-embed session log on close; auto-recall top-k into `## Recalled Context` on load (if `vector_memory.auto_recall_on_load: true`).
- **D0010** Search priority: SearXNG (SEARXNG_URL) → Brave (BRAVE_API_KEY) → DuckDuckGo (always available). Auto-probes and caches. Forceable via `web_search.backend`.
- **D0011** Content extraction: trafilatura → BeautifulSoup fallback. Truncated to `web_search.max_page_chars` (default 8000).
- **D0012** (decisions.json: D0004) Agent delegation via ReAct action `delegate[agent_type | task]`. Depth-limited to `delegation.max_depth` (default 3). Sub-agents run with `max_sub_iterations=5`. Delegator injected by Orchestrator; sub-agents inherit it at depth+1.
- **D0013** (decisions.json: D0005) Sub-context slimming: delegation passes task description + top-3 vector recall + 1000-char parent context slice. Never passes full parent ReAct trace — keeps sub-agents focused.
- **D0014** Telegram interface is a thin layer in orchid/interfaces/: TelegramBot (command dispatch), BackgroundRunner (thread pool, asyncio bridge), telegram_formatter (plain-text output). No business logic in bot — all calls go through Orchestrator and Session.
- **D0015** User whitelist security: TELEGRAM_ALLOWED_USERS env var (comma-separated user IDs). If set, bot silently drops messages from unknown users. If unset, warns on startup and accepts all (dev mode only).

## Key Files
```
orchid/orchid.defaults.yaml       all defaults
orchid/config.py                  load_defaults, configure_for_project, merge_for_project
orchid/orchestrator.py            main loop, task routing, agent dispatch
orchid/session.py                 state lifecycle, context loading, compression
orchid/agents/base.py             ReAct loop, tool registry, SIGALRM
orchid/agents/developer|researcher|reviewer.py  (delegation-capable via delegator injection)
orchid/agents/delegator.py            AgentDelegator — spawns sub-agents, depth-limits, embeds results
orchid/tools/models.py            call/route/embed (Claude/llama.cpp)
orchid/tools/filesystem|shell|search.py
orchid/memory/state.py            tasks.md+CLAUDE.md HTML-comment-aware parser
orchid/memory/decisions.py        JSON Lines append-only
orchid/memory/vector.py           ChromaDB embedded
orchid/interfaces/cli.py
orchid/interfaces/telegram_bot.py     TelegramBot — command handlers, auth guard
orchid/interfaces/telegram_formatter.py  plain-text/emoji output helpers
orchid/interfaces/background_runner.py   BackgroundRunner — thread pool + asyncio bridge
scripts/orchid-telegram.service.template  systemd unit template
```

## Install
```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env  # ANTHROPIC_API_KEY required
# llama.cpp: http://localhost:8080/v1 (override: LLAMA_BASE_URL)
```

## Task Status
| ID | Status | Summary |
|----|--------|---------|
| T001 | Done | session.py compression review |
| T002 | Done (max iter) | LLM summarizer hooked into compression |
| T003 | Done | Prior summary preserved on re-compression |
| T004 | Done | Multi-cycle compression tests added |
| T005 | Done | `_save()` contract documented in docstring |
| T006 | Done | Context window size wired to orchid.defaults.yaml |
| T007 | Incomplete | DDG ad-result filter (y.js URLs) — needs finish |
| T008 | Incomplete | decisions.json JSON Lines vs single-doc parse error — needs fix |

## Not Built Yet
- Slack interface (reserved — follows same pattern as Telegram)
- Multi-project parallelism (--multi mode stub present, not wired)
- SearXNG server setup (DDG fallback active)
## Milestone 2.4 — Telegram bot interface (2026-03-14)
Files added: telegram_bot.py, telegram_formatter.py, background_runner.py, tests/test_telegram.py, scripts/orchid-telegram.service.template
CLI: `orchid telegram --project <path>` — token from TELEGRAM_BOT_TOKEN env var
Decisions recorded: D0014 (thin layer architecture), D0015 (whitelist security model)

## Recent Completions

- [T009] Fix orchid task add subcommand - unexpected extra argument error: [max iterations reached without final answer]

- [T010] Research the best approach for implementing a retry mechanism in httpx, then implement a retry wrapper in orchid/tools/models.py using that approach: Thought: Let me try a different approach - I'll create the file using base64 encoding to avoid escaping issues.

Action: bash
Action Input: {"command": "base64 -d << 'EOF' > orchid/tools/models.py\nIi

- [T011] Fix developer agent prompt to use delegate action for research-first tasks: I've updated the developer agent prompt in `/home/dave/LocalAI/orchid/orchid/agents/developer.py` to include delegation guidelines. The prompt now explicitly instructs the developer agent to:

1. Use

- [T012] Fix decisions.json Extra data parse error - persists after T008: 

- [T013] Fix CLAUDE.md compression truncating decision entries: Thought: The issue is with escaping quotes in the f-string. Let me use a different approach - I'll write the file using base64 encoding to avoid all escaping issues.

Action: bash
Action Input: {"comm

- [T014] Research best practices for Python async context managers, then implement one in orchid/session.py for safe session lifecycle management: Thought: The nested string escaping is causing issues. Let me try a simpler approach - write the file using base64 encoding to avoid all escaping problems.

Action: bash
Action Input: {"command": "ech

- [T015] test task from Telegram: I'm unable to execute the test task because the tool execution system is experiencing a threading error ("signal only works in main thread of the main interpreter"). This is preventing me from:

1. Re

- [T016] test task from Telegram: The test task from Telegram has been executed successfully.

**Result:**
- Ran Orchid in autonomous mode against project `/home/dave/projects/webtron`
- Session loaded successfully with:
  - Tasks: 0
