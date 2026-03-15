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

## Key Files
```
orchid/orchid.defaults.yaml       all defaults
orchid/config.py                  load_defaults, configure_for_project, merge_for_project
orchid/orchestrator.py            main loop, task routing, agent dispatch
orchid/session.py                 state lifecycle, context loading, compression
orchid/agents/base.py             ReAct loop, tool registry, SIGALRM
orchid/agents/developer|researcher|reviewer.py
orchid/tools/models.py            call/route/embed (Claude/llama.cpp)
orchid/tools/filesystem|shell|search.py
orchid/memory/state.py            tasks.md+CLAUDE.md HTML-comment-aware parser
orchid/memory/decisions.py        JSON Lines append-only
orchid/memory/vector.py           ChromaDB embedded
orchid/interfaces/cli.py
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
- Telegram/Slack interfaces
- Agent-to-agent delegation
- Multi-project parallelism
- SearXNG server setup (DDG fallback active)
## Recent Completions

- [T009] Fix orchid task add subcommand - unexpected extra argument error: [max iterations reached without final answer]
