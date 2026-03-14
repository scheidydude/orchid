# CLAUDE.md — Orchid Framework Dev Handoff

## What this repo is
Orchid is a persistent AI agent orchestration framework designed to run on a
bare-metal Ubuntu AI server (128GB RAM, AMD GPU / ROCm). It routes work between
the Claude API (orchestration, review) and a local llama.cpp server
(bulk/draft/worker tasks). All state is file-based and git-trackable.

## Architecture decisions made

### D0001 — File-based state (no database)
State lives in `tasks.md`, `CLAUDE.md`, `.orchid/decisions.json`, and
`.orchid/session_logs/`. Reason: git-trackable, human-readable, zero infra.

### D0002 — Two-tier model routing
Claude API for orchestration/review/critique. Local llama.cpp for
draft/code_generate/summarize/search. Threshold configurable in
`orchid.config.yaml`. Reason: cost + latency.

### D0003 — ReAct agent loop
Agents use Reason→Act→Observe format parsed from model output.
Tool calls are JSON extracted from `Action Input: {...}`.
Reason: works with any model (no function-calling required).

### D0004 — Interface-agnostic core
`orchid/interfaces/` is a thin layer. CLI is first; Telegram/Slack can be
added without touching orchestrator or agents. Interface base class pattern
reserved in `interfaces/__init__.py`.

### D0005 — uv for dependency management
`uv venv` + `pyproject.toml`. No requirements.txt. Optional deps in extras:
`[dev]` for testing/linting, `[vector]` for Chroma.

## Project structure
```
orchid/
├── orchestrator.py      main loop, task routing, agent dispatch
├── session.py           state lifecycle, hot memory compression
├── config.py            YAML config loader with env var expansion
├── agents/
│   ├── base.py          ReAct base class, tool registry, dispatch
│   ├── developer.py     code agent (local model)
│   ├── researcher.py    search/summarize agent (local model)
│   └── reviewer.py      critic agent (Claude API)
├── memory/
│   ├── state.py         tasks.md + CLAUDE.md read/write
│   ├── decisions.py     JSON Lines decision log
│   └── vector.py        Chroma stub (not yet active)
├── tools/
│   ├── filesystem.py    read/write/list/append
│   ├── shell.py         bash with blocklist + timeout
│   └── models.py        unified call() + route() functions
└── interfaces/
    └── cli.py           typer CLI: run, chat, status, task, decide
```

## Key files
- `orchid.config.yaml` — model routing, memory paths, agent limits
- `.env.example` — `ANTHROPIC_API_KEY`, `LLAMA_BASE_URL`, log level
- `scripts/start_session.sh` — tmux launcher (chat + shell + logs windows)
- `projects/example/` — reference project with tasks.md and CLAUDE.md

## Local setup
```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env  # add ANTHROPIC_API_KEY
orchid status projects/example
```

## llama.cpp expected at
`http://localhost:8080/v1` (OpenAI-compatible). Override via `LLAMA_BASE_URL`.
Model name doesn't matter — llama.cpp ignores it and serves whatever is loaded.

## What's not yet built
- Vector memory (Chroma stub in `memory/vector.py`)
- Telegram/Slack interfaces (stub reserved in `interfaces/`)
- Web search tool (add to `tools/` + register in base.py schemas)
- Multi-project parallelism
- Agent-to-agent delegation (ReviewerAgent reviewing DeveloperAgent output)

## Testing
```bash
pytest tests/           # unit tests (no API calls)
pytest -v -k test_bash  # single test
```
Tests in `tests/test_state.py`, `tests/test_decisions.py`, `tests/test_tools.py`.
No mocks needed — tests use tmp_path for isolation.

## Current status
Phase 1 complete. All core modules implemented and tested.
Ready for first real project run.
