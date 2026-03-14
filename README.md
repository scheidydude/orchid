# Orchid

AI agent orchestration framework. Install once, run against any project.

```
~/orchid/              ← install here
~/projects/webtron/    ← your project (any git repo)
~/projects/blog/       ← another project
```

## Install

```bash
git clone git@github.com:dave/orchid.git ~/orchid
cd ~/orchid
uv venv && source .venv/bin/activate
uv pip install -e .

cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY
# llama.cpp is expected at http://localhost:8080/v1 (set LLAMA_BASE_URL to override)
```

## Quick start

```bash
# Scaffold a new project
cd ~/projects/webtron
orchid init .

# Or from anywhere
orchid init ~/projects/webtron --name webtron --description "2D space shooter"

# Check status
orchid --project ~/projects/webtron --status

# Add tasks
orchid --project ~/projects/webtron --add-task "Build player ship" --type code_generate --priority 1

# Run autonomously
orchid --project ~/projects/webtron --mode auto

# Interactive chat
orchid --project ~/projects/webtron --mode interactive

# Persistent tmux session
./scripts/start_session.sh ~/projects/webtron
```

## What a project looks like

After `orchid init`:

```
webtron/
├── CLAUDE.md        ← hot memory: context, decisions, current focus
├── tasks.md         ← task board: parsed by orchid, edited by you
├── .orchid.yaml     ← optional per-project config
└── .orchid/         ← runtime data (gitignored)
    ├── decisions.json
    └── session_logs/
```

### .orchid.yaml

```yaml
project: webtron
description: "2D space shooter web game"
model_preference: auto     # claude | local | auto

agent_roles:
  - developer
  - researcher
  - reviewer

context_files:             # extra files loaded into every agent prompt
  - README.md
  - docs/architecture.md

# memory:
#   compression_threshold: 8000
```

### tasks.md format

```markdown
- [ ] **T001** Build player ship `type:code_generate` `p1` `agent:developer`
  - src/player.py — movement, shooting, collision
- [ ] **T002** Write unit tests `type:code_generate` `p2`
- [ ] **T003** Review T001 `type:review` `p2` `agent:reviewer`
```

Task types: `code_generate` `draft` `review` `summarize` `search` `plan` `critique`
Priorities: `p1` high · `p2` normal · `p3` low

## CLI reference

```
orchid --project PATH --mode auto          run all pending tasks
orchid --project PATH --mode interactive   chat with agent
orchid --project PATH --status             task board + hot memory
orchid --project PATH --add-task "title"   add a task quickly
orchid --project PATH --add-task "title" --type review --priority 1

orchid init PATH [--name NAME] [--description TEXT] [--force]
orchid decide "Title" -d "Decision" -r "Rationale" --project PATH
orchid task add --title "..." --type code_generate --project PATH
orchid task done --id T001 --project PATH
orchid task block --id T002 --project PATH
```

## Architecture

```
orchestrator.py      main loop: pick task → plan (Claude) → dispatch agent
session.py           state lifecycle: load, save, compress hot memory
config.py            three-layer merge: defaults → .orchid.yaml → CLI flags

agents/
  base.py            ReAct loop (Reason → Act → Observe), tool dispatch
  developer.py       code generation/editing (local model)
  researcher.py      search and summarize (local model)
  reviewer.py        critique and quality gate (Claude API)

memory/
  state.py           tasks.md + CLAUDE.md read/write
  decisions.py       append-only JSON Lines decision log
  vector.py          Chroma stub (future)

tools/
  models.py          unified call() for Claude API + llama.cpp
  filesystem.py      read_file, write_file, list_dir, append_file
  shell.py           bash execution with blocklist + timeout

interfaces/
  cli.py             Typer CLI — Telegram/Slack can be added here later
```

## Model routing

| Task type | Default model |
|-----------|--------------|
| `orchestrate` `review` `plan` `critique` `synthesize` | Claude API |
| `draft` `code_generate` `summarize` `search` `transform` | Local llama.cpp |

Override per-project with `model_preference: claude` in `.orchid.yaml`.

## Development

```bash
pytest             # 26 tests, no API calls required
ruff check orchid/
```
