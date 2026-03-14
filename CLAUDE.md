# CLAUDE.md — Orchid Framework Dev Handoff

## What this repo is
Orchid is a standalone AI agent orchestration tool installed once on a server
and invoked against any external project directory. Projects are independent
git repos that opt in by having CLAUDE.md, tasks.md, and optionally .orchid.yaml.

## Architecture: Option B — standalone runtime

Orchid is NOT a monorepo. Projects live wherever they want:

```
~/orchid/          ← Orchid tool (this repo, installed once)
~/projects/
  webtron/         ← independent project repo
    CLAUDE.md
    tasks.md
    .orchid.yaml   ← optional per-project config
    .orchid/       ← runtime data (gitignored)
      decisions.json
      session_logs/
  blog/            ← another project
    ...
```

Usage:
```bash
orchid --project ~/projects/webtron --mode auto
orchid --project ~/projects/webtron --status
orchid init ~/projects/newproject
```

## Architecture decisions

### D0001 — File-based state (no database)
State in tasks.md, CLAUDE.md, .orchid/decisions.json, .orchid/session_logs/.
Git-trackable, human-readable, zero infra.

### D0002 — Two-tier model routing
Claude API for orchestrate/review/plan/critique/synthesize.
Local llama.cpp for draft/code_generate/summarize/search.
Threshold + routing configurable in orchid.defaults.yaml and per-project .orchid.yaml.

### D0003 — ReAct agent loop
Reason→Act→Observe text parsing. No function-calling API required — works
with any model that can follow format instructions.

### D0004 — Interface-agnostic core
orchid/interfaces/ is a thin layer. CLI first; Telegram/Slack slots in without
touching orchestrator or agents.

### D0005 — Three-layer config merge
1. `orchid/orchid.defaults.yaml` — bundled with package, all defaults
2. `<project>/.orchid.yaml` — per-project overrides
3. CLI flags — session-level overrides
`configure_for_project(path)` resets the global config singleton.

### D0006 — Option B standalone runtime (this refactor)
Projects are NOT subfolders of Orchid. Orchid is installed globally/in a venv.
Projects opt in with CLAUDE.md + tasks.md + optional .orchid.yaml.
`orchid init <path>` scaffolds the three files + updates .gitignore.

## Project structure
```
orchid/
├── orchid/
│   ├── orchid.defaults.yaml     bundled defaults (was orchid.config.yaml)
│   ├── templates/               used by orchid init
│   │   ├── CLAUDE.md
│   │   ├── tasks.md
│   │   └── .orchid.yaml
│   ├── config.py                load_defaults(), configure_for_project(), merge_for_project()
│   ├── orchestrator.py          main loop, task routing, agent dispatch
│   ├── session.py               state lifecycle, context_files loading, hot memory compression
│   ├── agents/
│   │   ├── base.py              ReAct loop, tool registry, SIGALRM dispatch
│   │   ├── developer.py         code agent (local model)
│   │   ├── researcher.py        search/summarize (local model)
│   │   └── reviewer.py          critic (Claude API)
│   ├── memory/
│   │   ├── state.py             tasks.md + CLAUDE.md, HTML comment-aware parser
│   │   ├── decisions.py         JSON Lines append-only log
│   │   └── vector.py            Chroma stub
│   ├── tools/
│   │   ├── models.py            call() + route() for Claude/llama.cpp
│   │   ├── filesystem.py        read/write/list/append
│   │   └── shell.py             bash with blocklist + SIGALRM timeout
│   └── interfaces/
│       └── cli.py               main callback (--mode/--status/--add-task) + init/decide/task subcommands
└── scripts/
    └── start_session.sh         tmux launcher
```

## CLI reference
```bash
# Main flags
orchid --project <path> --mode auto          # autonomous run
orchid --project <path> --mode interactive   # chat with agent
orchid --project <path> --status             # task board + hot memory
orchid --project <path> --add-task "title"   # add a task

# Subcommands
orchid init <path> [--name NAME] [--description TEXT] [--force]
orchid decide "Title" --decision "..." --rationale "..." --project <path>
orchid task add --title "..." --type code_generate --project <path>
orchid task done --id T001 --project <path>
```

## Config: .orchid.yaml schema
```yaml
project: myapp
description: "one-line description"
model_preference: auto   # claude | local | auto
agent_roles:
  - developer
context_files:           # extra files loaded into agent context
  - README.md
memory:
  compression_threshold: 8000
```

## Install
```bash
git clone ... ~/orchid && cd ~/orchid
uv venv && uv pip install -e ".[dev]"
cp .env.example .env    # add ANTHROPIC_API_KEY
```

## Local llama.cpp
Expected at http://localhost:8080/v1. Override: LLAMA_BASE_URL env var.

## Testing
```bash
pytest tests/
```

## Not yet built
- Chroma vector memory (stub: memory/vector.py)
- Telegram/Slack interfaces (reserved in interfaces/)
- Web search tool
- Agent-to-agent delegation
- Multi-project parallelism
