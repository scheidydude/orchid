# Getting Started with Orchid

Orchid is an AI agent orchestration tool. You install it once and point it at any project. It reads your project's task list, picks the next task, and runs an AI agent loop to complete it — writing code, searching the web, reviewing output, and recording decisions.

---

## Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- An Anthropic API key (for planning, review, and critique tasks)
- Optionally: [llama.cpp](https://github.com/ggerganov/llama.cpp) running locally on port 8080 (for code generation and drafting without the API)

---

## 1. Install Orchid

```bash
git clone git@github.com:dave/orchid.git ~/orchid
cd ~/orchid
uv venv && source .venv/bin/activate
uv pip install -e .

cp .env.example .env
```

Edit `.env` and set your API key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

If you have llama.cpp running elsewhere, also set:

```
LLAMA_BASE_URL=http://localhost:8080/v1
```

Verify the install worked:

```bash
orchid --help
```

---

## 2. Scaffold a Project

Navigate to your project directory (any existing repo, or a new empty folder) and run:

```bash
orchid init ~/projects/myapp --name myapp --description "A brief description of your project"
```

This creates three files in your project:

```
myapp/
├── CLAUDE.md        ← agent context: describe your project here
├── tasks.md         ← task board: add tasks here
└── .orchid.yaml     ← optional per-project config
```

`.orchid/` is also created for runtime data (session logs, vector memory). It is gitignored automatically.

---

## 3. Write Your CLAUDE.md

`CLAUDE.md` is loaded into every agent prompt as context. Fill it in before running tasks — the more relevant context you provide, the better the agent performs.

```markdown
# CLAUDE.md — myapp

## Project Overview
A REST API for tracking inventory. Built with FastAPI and PostgreSQL.

## Current Focus
Implement the product endpoints and wire up the database layer.

## Architecture Notes
- src/api/ — FastAPI routers
- src/db/ — SQLAlchemy models and session
- Tests live in tests/ and use pytest with a real test database

## Context
- Use snake_case for all Python identifiers
- All endpoints must be authenticated via the existing JWT middleware in src/auth.py
```

Orchid will automatically compress this file when it grows too large.

---

## 4. Add Tasks

Open `tasks.md` and add tasks in this format:

```markdown
# Tasks

## TODO

- [ ] **T001** Create product model `type:code_generate` `p1` `agent:developer`
  - Add SQLAlchemy model in src/db/models.py with fields: id, name, sku, quantity, price
- [ ] **T002** Create product CRUD endpoints `type:code_generate` `p1` `agent:developer`
  - GET /products, POST /products, PUT /products/{id}, DELETE /products/{id}
  - Depends on T001
- [ ] **T003** Write tests for product endpoints `type:code_generate` `p2` `agent:developer`
- [ ] **T004** Review product API `type:review` `p2` `agent:reviewer`
```

You can also add tasks from the command line:

```bash
orchid --project ~/projects/myapp --add-task "Create product model" --type code_generate --priority 1
```

### Task format reference

| Field | Values | Description |
|-------|--------|-------------|
| `type:` | `code_generate` `draft` `review` `summarize` `search` `plan` `critique` | What kind of work |
| `p1` / `p2` / `p3` | high / normal / low | Priority |
| `agent:` | `developer` `researcher` `reviewer` `base` | Which agent to use |
| `needs:T001,T002` | task IDs | Dependencies (won't run until those are done) |
| `model:claude` | `claude` `local` | Force a specific model for this task |

All fields except the task title are optional.

---

## 5. Run Orchid

### Check what's pending

```bash
orchid --project ~/projects/myapp --status
```

### Run all tasks automatically

```bash
orchid --project ~/projects/myapp --mode auto
```

Orchid picks the highest-priority pending task, dispatches an agent, loops until it produces a final answer, then moves on to the next task.

### Run without the Anthropic API (local models only)

```bash
orchid --project ~/projects/myapp --mode auto --offline
```

This forces all tasks to route to your local llama.cpp instance. Review and critique tasks that normally use Claude will also run locally.

### Interactive chat

```bash
orchid --project ~/projects/myapp --mode interactive
```

Chat directly with an agent. Useful for exploration or one-off questions about your codebase.

---

## 6. Watch What's Happening

Orchid writes a live log to `.orchid/session_logs/`. To tail it in another terminal:

```bash
orchid --project ~/projects/myapp --tail
```

You can also inject a message mid-run (e.g. to redirect the agent):

```bash
orchid --project ~/projects/myapp --inject "Focus on error handling, ignore the happy path for now"
```

---

## 7. Record Decisions

When you make an architectural decision, record it so future agents have context:

```bash
orchid decide "Use PostgreSQL over SQLite" \
  --decision "We will use PostgreSQL as the database" \
  --rationale "The app needs concurrent writes and row-level locking" \
  --project ~/projects/myapp
```

Decisions are stored in `.orchid/decisions.json` and surfaced to agents automatically.

---

## 8. Mark Tasks Done or Blocked

```bash
orchid task done --id T001 --project ~/projects/myapp
orchid task block --id T002 --project ~/projects/myapp
```

---

## 9. Web UI (Optional)

Orchid ships a browser-based UI for managing projects, watching agent runs live, and browsing session history.

**Start a persistent server** that auto-discovers all orchid projects under a directory:

```bash
orchid serve --watch-dir ~/projects --port 7842
```

Open **http://localhost:7842** in your browser.

**Or start for a single project:**

```bash
orchid web --project ~/projects/myapp
```

### Run as a systemd service

```bash
bash scripts/install-orchid-serve.sh
sudo journalctl -u orchid-serve -f
```

---

## 10. Provider Configuration

By default Orchid routes tasks to two backends:

| Tasks | Backend |
|-------|---------|
| `plan` `review` `critique` `orchestrate` `synthesize` | Claude API (`ANTHROPIC_API_KEY`) |
| `code_generate` `draft` `summarize` `search` | llama.cpp at `localhost:8080` |

You can override this per-project in `.orchid.yaml`:

```yaml
model_preference: claude   # send everything to Claude
# or
model_preference: local    # send everything to local model
```

Or per-task with the `model:` tag in `tasks.md`:

```markdown
- [ ] **T005** Complex parser `type:code_generate` `p1` `model:claude`
```

To check what providers are available and reachable:

```bash
orchid --check-providers
```

---

## Workflow Summary

```
1. orchid init <path>         scaffold CLAUDE.md + tasks.md
2. Edit CLAUDE.md             give the agent project context
3. Edit tasks.md              describe what needs to be done
4. orchid --project <path> --status    confirm tasks are parsed correctly
5. orchid --project <path> --mode auto    let it run
6. Review output, mark done, add more tasks, repeat
```

---

## Common Issues

**"ANTHROPIC_API_KEY not set"**
Set it in `.env` at the orchid root, or export it in your shell. Use `--offline` to skip Claude entirely.

**"Provider unavailable: local"**
llama.cpp is not running. Start it on port 8080, or use `--code-model claude` to route all tasks to the API.

**Task stays in TODO**
Check for unmet dependencies (`needs:` tags) or a `BLOCKED` status. Run `--status` to see the full board.

**Agent hits max iterations without finishing**
The task may be too broad. Break it into smaller atomic tasks — one clear deliverable per task ID works best.
