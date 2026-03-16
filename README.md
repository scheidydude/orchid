# Orchid

AI agent orchestration framework. Install once, run against any project.

"Orchid is a symbiotic ecosystem of specialized AI agents, cultivated and orchestrated to transform ideas into reliable software systems."

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

orchid serve --watch-dir ~/LocalAI --port 7842   persistent multi-project server
orchid web --project PATH [--port 7842]           single-session web UI
orchid telegram --project PATH                    Telegram bot interface
orchid slack --project PATH                       Slack bot interface
```

## Web UI

Orchid ships a React-based web interface for managing projects, tasks, agent
runs, vector memory recall, and session history.

### Starting the web UI

**Persistent server with auto-discovery** (recommended):

```bash
# Scans ~/LocalAI and ~/Documents/Development for orchid projects automatically
orchid serve --watch-dir ~/LocalAI --watch-dir ~/Documents/Development

# Explicit projects
orchid serve --project ~/myapp --project ~/other/project
```

**Single or multi-project**:

```bash
orchid web --project ~/projects/webtron
orchid web --project ~/projects/webtron --project ~/projects/blog
```

Open **http://localhost:7842** in your browser.

### Features

- **Task board** — view, create, and update task status
- **Agent stream** — live WebSocket feed of agent activity during a run
- **Decision log** — full history of architectural decisions
- **Session history** — browse and replay past agent session logs
- **Vector recall** — semantic search over embedded session memory
- **Hot memory** — view the project's CLAUDE.md context in real time
- **Run controls** — start and stop agent runs from the browser
- **Auto-discovery** — projects in watched directories appear automatically;
  adding `.orchid.yaml` to a directory (via `orchid init`) registers it
  within seconds without restarting the server
- **Project switcher** — sidebar shows all projects with task counts,
  filesystem path, last session timestamp, and live status indicator

### Running as a systemd service

A ready-to-install service file is provided:

```bash
# Install and start
sudo cp scripts/orchid-serve.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now orchid-serve

# Or use the install script
bash scripts/install-orchid-serve.sh

# Logs
sudo journalctl -u orchid-serve -f
```

The service watches `/home/dave/LocalAI` and `/home/dave/Documents/Development`
by default. Edit `scripts/orchid-serve.service` to change watch directories.

### Building the frontend

The compiled frontend (`web_ui/dist/`) is included in installed packages.
If you are running from source or need to rebuild after UI changes:

```bash
cd orchid/interfaces/web_ui
npm install       # first time only
npm run build     # outputs to web_ui/dist/
```

**When installing via uv tool install, always build first:**

```bash
cd orchid/interfaces/web_ui && npm run build
cd ~/LocalAI/orchid && uv tool install . --force
```

### Development mode

Run the Vite dev server alongside the FastAPI backend for hot-reload:

```bash
# Terminal 1 — backend
orchid web --project ~/projects/webtron --dev

# Terminal 2 — frontend (proxies API calls to :7842)
cd orchid/interfaces/web_ui
npm run dev
# Open http://localhost:5173
```

### Frontend stack

**Frameworks and core libraries**

| Library | Version | Role |
|---------|---------|------|
| React | 18 | UI component framework |
| React DOM | 18 | Browser rendering |

**Build tooling**

| Tool | Version | Role |
|------|---------|------|
| Vite | 5 | Dev server, bundler, HMR |

**Vite plugins and add-ons**

| Plugin | Version | Role |
|--------|---------|------|
| @vitejs/plugin-react | 4 | JSX transform and React Fast Refresh |

**Runtime: none beyond React** — no routing library, no state management
library, no CSS framework. Styles are a single hand-written `index.css`
using CSS custom properties (variables) for the dark theme.

**Backend serving:** FastAPI `StaticFiles` serves `web_ui/dist/` in
production. The SPA catch-all route returns `index.html` for all
non-API paths.

## Architecture

```
orchestrator.py      main loop: pick task → plan (Claude) → dispatch agent
session.py           state lifecycle: load, save, compress hot memory
config.py            three-layer merge: defaults → .orchid.yaml → CLI flags
discovery.py         auto-discovery: scan watch_dirs, watchdog inotify watcher
agent_manager.py     per-project agent loops, APScheduler cron support

agents/
  base.py            ReAct loop (Reason → Act → Observe), tool dispatch
  developer.py       code generation/editing (local model)
  researcher.py      search and summarize (local model)
  reviewer.py        critique and quality gate (Claude API)

memory/
  state.py           tasks.md + CLAUDE.md read/write
  decisions.py       append-only JSON Lines decision log
  vector.py          ChromaDB embedded vector store

tools/
  models.py          unified call() for Claude API + llama.cpp
  filesystem.py      read_file, write_file, list_dir, append_file
  shell.py           bash execution with blocklist + timeout

interfaces/
  cli.py             Typer CLI entry point
  web_server.py      FastAPI REST + WebSocket backend
  web_ui/            React + Vite frontend source
  telegram_bot.py    Telegram bot (python-telegram-bot)
  slack_bot.py       Slack bot (slack-bolt, Socket Mode)
  background_runner.py  non-blocking executor shared by Telegram and Slack

providers/
  registry.py        5-layer provider resolution chain
  anthropic.py       Claude API (with exponential backoff on 429)
  local.py           llama.cpp OpenAI-compat endpoint
  ollama.py          Ollama
  openai.py          OpenAI / OpenRouter
  bedrock.py         AWS Bedrock (boto3, lazy import)
```

## Model routing

| Task type | Default model |
|-----------|--------------|
| `orchestrate` `review` `plan` `critique` `synthesize` | Claude API |
| `draft` `code_generate` `summarize` `search` `transform` | Local llama.cpp |

Override per-project with `model_preference: claude` in `.orchid.yaml`.

## Development

```bash
pytest             # 227 tests, no API calls required
ruff check orchid/
```
