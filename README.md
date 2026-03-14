# Orchid

AI agent orchestration framework for persistent bare-metal deployment.

Runs continuously on a server, processes tasks from a file-based board,
routes work to Claude API or a local llama.cpp instance, and tracks all
state in git-trackable files.

## Quick Start

```bash
# 1. Install dependencies
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY, LLAMA_BASE_URL

# 3. Run against the example project
orchid status projects/example
orchid run projects/example

# 4. Or start an interactive session
orchid chat projects/example

# 5. Or launch a persistent tmux session
./scripts/start_session.sh projects/example
```

## Commands

| Command | Description |
|---------|-------------|
| `orchid run [project]` | Autonomous mode — process all pending tasks |
| `orchid chat [project]` | Interactive chat with an agent |
| `orchid status [project]` | Show task board and hot memory |
| `orchid task add --title "..." --type code_generate` | Add a task |
| `orchid task done --id T001` | Mark task done |
| `orchid decide "Title" -d "Decision" -r "Rationale"` | Log a decision |

## Architecture

```
orchid/
├── orchestrator.py      # Main loop: pick task → plan → dispatch agent
├── session.py           # State load/save, hot memory compression
├── agents/
│   ├── base.py          # ReAct loop (Reason → Act → Observe)
│   ├── developer.py     # Code-focused (local model)
│   ├── researcher.py    # Search/summarize (local model)
│   └── reviewer.py      # Critique/review (Claude API)
├── memory/
│   ├── state.py         # tasks.md + CLAUDE.md read/write
│   ├── decisions.py     # Append-only decision log
│   └── vector.py        # Chroma stub (future)
├── tools/
│   ├── filesystem.py    # read_file, write_file, list_dir
│   ├── shell.py         # bash (sandboxed)
│   └── models.py        # Unified API caller + routing
└── interfaces/
    └── cli.py           # Typer CLI (Telegram/Slack later)
```

## Model Routing

| Task type | Model |
|-----------|-------|
| `orchestrate`, `review`, `plan`, `critique`, `synthesize` | Claude API |
| `draft`, `search`, `transform`, `summarize`, `code_generate` | Local llama.cpp |

Override routing in `orchid.config.yaml` or set `ORCHID_WORKER_MODEL=claude`.

## State Files

All state is file-based and git-trackable:

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Hot memory — loaded into every agent prompt |
| `tasks.md` | Task board — parsed task queue |
| `.orchid/decisions.json` | Append-only architectural decision log |
| `.orchid/session_logs/` | JSONL event log per session |

## Adding a New Project

```bash
mkdir -p projects/myproject
echo "# CLAUDE.md\n\nProject context here." > projects/myproject/CLAUDE.md
echo "# Tasks\n" > projects/myproject/tasks.md
orchid task add --project projects/myproject --title "First task" --type draft
orchid run projects/myproject
```

## Development

```bash
uv pip install -e ".[dev]"
pytest
ruff check orchid/
```
