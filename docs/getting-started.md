# Getting Started with Orchid

Orchid is an AI agent orchestration tool. You install it once and point it at any project. In V2, it guides you through a full planning workflow — discuss requirements with an AI product manager, generate architecture and task documents, then execute tasks with specialized agents.

---

## Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- Optionally: an Anthropic API key — used by default for planning, discussion, review, and critique agents. All agents can be redirected to a local model via `providers.agent_defaults` in `.orchid.yaml`; no API key is required for fully-local operation.
- Optionally: [llama.cpp](https://github.com/ggerganov/llama.cpp) running locally on port 8080 (for code generation, drafting, and any agent overridden to `local`)

---

## 1. Install Orchid

```bash
git clone git@github.com:scheidydude/orchid.git ~/LocalAI/orchid
cd ~/LocalAI/orchid
uv tool install .

# Config lives at ~/.config/orchid/.env (XDG standard, chmod 600)
bash scripts/setup-config.sh
```

Edit `~/.config/orchid/.env` and set your API key:

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

## 2. Create a Project

### Option A — New project wizard

```bash
orchid new "A REST API for inventory tracking" --name myapp
```

This prompts for confirmation, scaffolds the project directory, and starts the planning workflow automatically.

### Option B — Scaffold into an existing repo

```bash
orchid init ~/projects/myapp --name myapp --description "A brief description of your project"
```

Both approaches create:

```
myapp/
├── CLAUDE.md        ← agent context: describe your project here
├── tasks.md         ← task board: add tasks here
└── .orchid.yaml     ← optional per-project config
```

`.orchid/` is also created for runtime data (session logs, vector memory). It is gitignored automatically.

---

## 3. V2 Planning Workflow

Orchid V2 uses a lifecycle state machine to guide a project from idea to running code:

```
NEW → DISCUSSING → REQUIREMENTS → PLANNING → READY → EXECUTING → COMPLETE
```

Check the current phase at any time:

```bash
orchid --project ~/projects/myapp --phase
```

### Step 1 — Discuss requirements

```bash
orchid --project ~/projects/myapp --discuss
```

This starts an interactive chat with the DiscussionAgent (Claude). The agent asks clarifying questions about your goals, constraints, and technical preferences. You can exit and resume at any time — conversation history is persisted.

Example session:

```
Orchid: What problem are you solving and who are your primary users?
You: A REST API for tracking inventory. Used by warehouse staff on tablets.
Orchid: What existing systems does it need to integrate with?
You: Our ERP exports CSV. We need to import those and expose data via JSON API.
Orchid: Any authentication requirements?
You: JWT, managed by our existing auth service. We just need to validate tokens.
```

When you're satisfied, type `done` or `exit` to end the discussion.

### Step 2 — Generate planning artifacts

```bash
orchid --project ~/projects/myapp --approve
```

This advances the lifecycle gate. From DISCUSSING it triggers:

1. **ProductManagerAgent** → writes `REQUIREMENTS.md` and `ARCHITECTURE.md`
2. **ProjectManagerAgent** → writes `MILESTONES.md` and populates `tasks.md`

Review the generated documents and run `--approve` again to advance to READY.

```bash
orchid --project ~/projects/myapp --artifacts    # check what was generated
cat ~/projects/myapp/REQUIREMENTS.md
cat ~/projects/myapp/tasks.md
```

### Step 3 — Execute tasks

```bash
orchid --project ~/projects/myapp --mode auto
```

Orchid picks the highest-priority pending task, routes it to the right agent and model, runs the ReAct loop, stores the result, and moves on. Each task's output is saved and retrievable:

```bash
orchid --project ~/projects/myapp --get-result T001
```

### Running a Single Task

To run just one specific task without executing all pending tasks:

```bash
orchid --project ~/projects/myapp --run-task T015
```

This is useful for:
- Testing a specific task in isolation
- Re-running a failed task
- Executing tasks out of order when dependencies allow

---

## 4. Write or Edit CLAUDE.md

`CLAUDE.md` is loaded into every agent prompt as context. For projects created with `orchid init` (not the wizard), fill it in before running tasks — the more relevant context you provide, the better the agent performs.

```markdown
# CLAUDE.md — myapp

## Project Overview
A REST API for tracking inventory. Built with FastAPI and PostgreSQL.

## Current Focus
Implement the product endpoints and wire up the database layer.

## Architecture Note
- src/api/ — FastAPI routers
- src/db/ — SQLAlchemy models and session
- Tests live in tests/ and use pytest with a real test database

## Context
- Use snake_case for all Python identifiers
- All endpoints must be authenticated via the existing JWT middleware in src/auth.py
```

Orchid will automatically compress this file when it grows too large.

---

## 5. Add Tasks Manually

You can also skip the planning workflow and add tasks directly to `tasks.md`:

```markdown
# Tasks

## TODO

- [ ] **T001** Create product model `type:code_generate` `p1` `agent:developer`
  - Add SQLAlchemy model in src/db/models.py with fields: id, name, sku, quantity, price
- [ ] **T002** Create product CRUD endpoints `type:code_generate` `p1` `agent:developer`
  - GET /products, POST /products, PUT /products/{id}, DELETE /products/{id}
  - `needs:T001`
- [ ] **T003** Write tests for product endpoints `type:code_generate` `p2` `agent:developer`
- [ ] **T004** Review product API `type:review` `p2` `agent:reviewer`
- [~] **T005** Skip this feature `type:draft` `p3`  # skipped task
- [ ] **T099** Sprint rollup `type:rollup` `rollup:T001,T002,T003,T004` `output:SPRINT1.md`
```

Or from the command line:

```bash
orchid --project ~/projects/myapp --add-task "Create product model" --type code_generate --priority 1
```

### Task format reference

| Field | Values | Description |
|-------|--------|-------------|
| `type:` | `code_generate` `draft` `review` `summarize` `search` `plan` `critique` `synthesize` `rollup` `verify` | What kind of work |
| `p1` / `p2` / `p3` | high / normal / low | Priority |
| `agent:` | `developer` `researcher` `reviewer` `base` | Which agent to use |
| `needs:T001,T002` | task IDs | Dependencies (won't run until those are done) |
| `model:claude` | `claude` `local` `auto` | Force a specific model for this task |
| `rollup:T001,T002` | task IDs | Sources for rollup synthesis |
| `output:FILE.md` | filename | Rollup output file |

All fields except the task title are optional.

### Task Status

Tasks can have the following statuses:

| Status | Syntax | Description |
|--------|--------|-------------|
| TODO | `[ ]` | Pending execution |
| DONE | `[x]` | Completed successfully |
| SKIP | `[~]` | Skipped — excluded from auto runs, satisfies dependencies |
| BLOCKED | `[!]` | Blocked — cannot proceed due to external factors |

### Skipping Tasks

To skip a task (e.g., a feature you've decided not to implement):

```bash
orchid task skip --id T005 --project ~/projects/myapp
```

Or manually change the checkbox in `tasks.md`:

```markdown
- [~] **T005** This feature is no longer needed `type:draft` `p3`
```

Skipped tasks:
- Are excluded from `--mode auto` runs
- Count as satisfied for dependency checks (tasks depending on them can proceed)
- Remain visible in the task board for reference

### Verify tasks

A `verify` task runs the TesterAgent — a dedicated QA agent that runs tests and reports structured results without writing any code:

```markdown
- [ ] **T006** Verify product endpoints `type:verify` `p2` `needs:T002`
```

The TesterAgent auto-detects the project environment (docker/venv/node/python) and uses the correct test runner. Output is structured JSON:
```json
{"passed": true, "tests_run": 42, "failures": [], "files_checked": ["tests/test_products.py"]}
```

To automatically create a paired verify task after every `code_generate` task completes, add to `.orchid.yaml`:
```yaml
auto_verify: true
```

For environments without full test dependencies, `verify_syntax_only: true` runs only `py_compile` / `node --check` (no pytest/jest):
```yaml
verify_syntax_only: true
```

### Rollup tasks

A `rollup` task gathers the saved results from a set of completed tasks and synthesises a summary via Claude:

```markdown
- [ ] **T099** Sprint summary `type:rollup` `rollup:T001,T002,T003` `output:SPRINT1.md`
```

Rollup always uses Claude regardless of routing config.

### Routing simple vs. complex tasks

By default, `code_generate` and `draft` tasks go to your local model (llama.cpp/Ollama), and `review`/`plan`/`critique` go to Claude. To send a specific task to a more capable model, add `model:claude`:

```markdown
- [ ] **T010** Add CRUD endpoints `type:code_generate` `p2`
- [ ] **T011** Implement OAuth2 + JWT with refresh token rotation `type:code_generate` `p1` `model:claude`
```

Use `model:local` to force local even for task types that normally use Claude:

```markdown
- [ ] **T012** Quick offline review `type:review` `p2` `model:local`
```

---

## 6. Run Orchid

### Check what's pending

`--project` defaults to the current directory, so if you're already inside the project:

```bash
cd ~/projects/myapp
orchid --status     # same as orchid --project ~/projects/myapp --status
```

Or with the explicit flag:

```bash
orchid --project ~/projects/myapp --status
```

### Run all tasks automatically

```bash
orchid --project ~/projects/myapp --mode auto
```

Orchid picks the highest-priority pending task, dispatches an agent, loops until it produces a final answer, then moves on to the next task.

### Run a single task

```bash
orchid --project ~/projects/myapp --run-task T015
```

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

### Debug a stuck agent

Add `--trace` to log every ReAct iteration's raw thought/action/observation:

```bash
orchid --project ~/projects/myapp --run-task T015 --trace
```

This is useful when an agent is looping or not calling the right tools.

---

## 7. Watch What's Happening

Orchid writes a live log to `.orchid/session_logs/`. To tail it in another terminal:

```bash
orchid --project ~/projects/myapp --tail
```

You can also inject a message mid-run (e.g. to redirect the agent):

```bash
orchid --project ~/projects/myapp --inject "Focus on error handling, ignore the happy path for now"
```

---

## 8. Record Decisions

When you make an architectural decision, record it so future agents have context:

```bash
orchid decide "Use PostgreSQL over SQLite" \
  --decision "We will use PostgreSQL as the database" \
  --rationale "The app needs concurrent writes and row-level locking" \
  --project ~/projects/myapp
```

Decisions are stored in `.orchid/decisions.json` and surfaced to agents automatically.

---

## 9. Mark Tasks Done, Blocked, or Skipped

```bash
orchid task done --id T001 --project ~/projects/myapp
orchid task block --id T002 --project ~/projects/myapp
orchid task skip --id T005 --project ~/projects/myapp
```

Retrieve a task's stored output:

```bash
orchid --project ~/projects/myapp --get-result T001
```

---

## 10. Web UI (Optional)

Orchid ships a browser-based UI for managing projects, running the planning workflow, watching agent runs live, and browsing session history.

**Start a persistent server** that auto-discovers all orchid projects under a directory:

```bash
orchid serve --watch-dir ~/projects --port 7842
```

Open **http://localhost:7842** in your browser.

**Or start for a single project:**

```bash
orchid web --project ~/projects/myapp
```

### PM Dashboard Tab

The **PM Dashboard tab** provides quantitative project health metrics drawn from `.orchid/task_metrics.jsonl`:

- **MilestoneProgress** — task groups by milestone with completion % bars
- **DependencyGraph** — DAG visualization with color-coded task status and critical-path highlighting
- **SessionBurndown** — bar chart of tasks completed per session
- **PhaseTimeline** — lifecycle phase duration visualization
- **TaskTiming** — sortable table of all tasks sorted by duration, with iteration efficiency color coding (green = efficient, red = high iteration count)

### Planning Tab

The **Planning tab** exposes the full V2 workflow:

- **Phase indicator** — shows current lifecycle phase (NEW, DISCUSSING, REQUIREMENTS, PLANNING, READY, EXECUTING, COMPLETE)
- **Discussion chat** — chat with the AI PM with streaming responses
- **Discussion history** — view previous conversations and context
- **Artifact viewer** — browse REQUIREMENTS.md, ARCHITECTURE.md, MILESTONES.md
- **Gate approval panel** — advance through lifecycle gates
- **NewProject wizard** — create new projects from the browser

### Project Config Tab

The **Project Config tab** provides a dedicated interface for managing per-project settings:

- View and edit `.orchid.yaml` configuration
- Set model preferences (claude/local/auto)
- Configure agent roles
- Manage context files loaded into agent prompts
- Override gate behaviour (auto vs human approval)
- Shell mode settings (blocklist/allowlist)

Changes are saved immediately and reflected in subsequent agent runs.

### Task Board

The task board in the Web UI lets you:

- View all tasks with their status, priority, and dependencies
- Create new tasks directly from the browser
- Update task status (done, blocked, skipped)
- Skip tasks with a single click (marked as `[~]`)
- Run individual tasks with the ▶ button

### Active/Inactive Project Grouping

Projects in the sidebar are automatically grouped by activity status:

- **Active projects** — those with recent sessions or pending tasks
- **Inactive projects** — projects with no recent activity

This grouping helps you quickly focus on projects that need attention while keeping completed or dormant projects accessible but visually separated.

### Run as a systemd service

```bash
bash scripts/install-orchid-serve.sh
sudo journalctl -u orchid-serve -f
```

---

## 11. Provider Configuration

By default Orchid routes tasks to two backends:

| Tasks | Backend |
|-------|---------|
| `plan` `review` `critique` `orchestrate` `synthesize` `rollup` | Claude API (`ANTHROPIC_API_KEY`) |
| `code_generate` `draft` `summarize` `search` | llama.cpp at `localhost:8080` |

You can override this per-project in `.orchid.yaml`:

```yaml
providers:
  developer: ollama   # use Ollama instead of llama.cpp for developer agent

model_preference: claude   # or: send everything to Claude
```

Or per-task with the `model:` tag in `tasks.md`:

```markdown
- [ ] **T005** Complex parser `type:code_generate` `p1` `model:claude`
```

To check what providers are available and reachable:

```bash
orchid --check-providers
```

To use a specific provider for one run:

```bash
orchid --project PATH --mode auto --provider developer=ollama
```

---

## 12. Shell Safety Mode

By default Orchid's `bash` tool blocks known-dangerous commands (`rm -rf /`, `mkfs`, `dd if=`, fork bombs, etc.) while allowing everything else. For stricter control you can switch to **allowlist mode**, which only permits a curated set of executables:

```yaml
# .orchid.yaml
agents:
  shell_mode: allowlist          # blocklist (default) | allowlist
  shell_allowlist:               # add project-specific extras
    - make
    - docker
```

Built-in allowlist covers the tools agents typically need: `git`, `python`, `python3`, `uv`, `pytest`, `ruff`, `node`, `npm`, `npx`, `cargo`, `make`, `cmake`, file inspection (`cat`, `ls`, `find`, `grep`, `diff`), archive tools, and more. Blocklist patterns always apply regardless of mode.

---

## 13. Central Bot Server (V2.1)

Orchid V2.1 introduces a **central bot server** that unifies Telegram and Slack bot management under a single `orchid serve` command.

### Starting the Central Bot Server

```bash
# Start the central server with both Telegram and Slack bots
orchid serve --bots --port 7842

# Start only Telegram bot
orchid serve --telegram --port 7842

# Start only Slack bot
orchid serve --slack --port 7842

# Combine with project watching
orchid serve --bots --watch-dir ~/LocalAI --watch-dir ~/Documents/Development
```

### Environment Variables

Configure bot tokens in `~/.config/orchid/.env`:

```bash
# Telegram bot token (required for --telegram or --bots)
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11

# Slack bot token (required for --slack or --bots)
SLACK_BOT_TOKEN=xoxb-1234567890-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx
```

### Telegram Commands

Telegram bot commands use **underscores**:

| Command | Description |
|---------|-------------|
| `/orchid_start` | Start a new orchid session in the current project |
| `/orchid_status` | Show task board and current phase |
| `/orchid_projects` | List all available projects |
| `/orchid_switch <project>` | Switch to a different project |
| `/orchid_phase` | Show current lifecycle phase |
| `/orchid_tasks` | List pending tasks |
| `/orchid_approve` | Approve current lifecycle gate |
| `/orchid_discuss` | Start discussion with AI PM |
| `/orchid_help` | Show available commands |

### Slack Commands

Slack bot commands use **hyphens**:

| Command | Description |
|---------|-------------|
| `/orchid-status` | Show task board and current phase |
| `/orchid-projects` | List all available projects |
| `/orchid-switch <project>` | Switch to a different project |
| `/orchid-phase` | Show current lifecycle phase |
| `/orchid-tasks` | List pending tasks |
| `/orchid-approve` | Approve current lifecycle gate |
| `/orchid-discuss` | Start discussion with AI PM |
| `/orchid-help` | Show available commands |

### Channel Routing

The central bot supports **channel-to-project mapping** for team workflows. Each channel can be bound to a specific project, so commands in that channel automatically target the bound project.

---

## 14. Auth & Multi-User Support (V2.3)

Orchid's web server ships a complete auth stack. It's **opt-in**: without `JWT_SECRET` set, the server runs unauthenticated (localhost-only use case).

### Quick Setup

Add to `~/.config/orchid/.env`:

```bash
JWT_SECRET=<random-64-char-string>   # generate with: openssl rand -hex 32
```

Register the first (admin) user via the API:

```bash
curl -s -X POST http://localhost:7842/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "strong-password", "role": "admin"}'

curl -s -X POST http://localhost:7842/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "strong-password"}'
# → sets orchid_access and orchid_refresh HttpOnly cookies
```

### Token Flow

```
POST /api/auth/login     → orchid_access cookie (JWT, 15 min)
                           orchid_refresh cookie (opaque, 30 days)

GET  /api/*              → Bearer <token> header OR orchid_access cookie
POST /api/auth/refresh   → rotate refresh token, issue new pair
POST /api/auth/logout    → revoke refresh token, clear cookies
```

### API Keys (for CI / Scripts)

```bash
# Create a scoped key (secret returned once — store it)
curl -s -X POST http://localhost:7842/api/auth/apikeys \
  -H "Content-Type: application/json" \
  -d '{"name": "github-actions", "scopes": ["tasks:run"]}'

# Use it
curl -H "Authorization: Bearer ok_<key>" http://localhost:7842/api/auth/me
```

Available scopes: `tasks:run`, `tasks:read`, `*` (wildcard). Interactive JWT sessions bypass scope checks entirely.

### OAuth / SSO (Google, Entra, OIDC)

Configure providers in `~/.config/orchid/config.yaml`:

```yaml
auth:
  providers:
    - type: google
      client_id: "${GOOGLE_CLIENT_ID}"
      client_secret: "${GOOGLE_CLIENT_SECRET}"
      redirect_uri: "https://your-host/api/auth/oauth/google/callback"

    - type: entra
      tenant_id: "${AZURE_TENANT_ID}"
      client_id: "${AZURE_CLIENT_ID}"
      client_secret: "${AZURE_CLIENT_SECRET}"
      redirect_uri: "https://your-host/api/auth/oauth/entra/callback"

    - type: oidc
      name: "company-sso"
      discovery_url: "https://sso.company.com/.well-known/openid-configuration"
      client_id: "${SSO_CLIENT_ID}"
      client_secret: "${SSO_CLIENT_SECRET}"
      redirect_uri: "https://your-host/api/auth/oauth/company-sso/callback"
```

Users who sign in via OAuth for the first time get a new Orchid account. If their email already exists, the OAuth account is linked to it.

### Mobile PKCE Flow

```
Mobile generates code_verifier + code_challenge (S256)

GET  /api/auth/oauth/{provider}/start?code_challenge=...&code_challenge_method=S256
     → 302 to provider (challenge forwarded)

Provider → deep link: orchid://auth/callback?code=...&state=...

POST /api/auth/oauth/{provider}/token
     {code, state, code_verifier}
     ← {access_token, refresh_token, token_type, expires_in}
```

No client secret ever leaves the server. PKCE S256 is verified server-side before the provider exchange.

### User Management (Admin)

```bash
# Update role, projects, or active status
curl -X PUT http://localhost:7842/api/auth/users/alice \
  -H "Authorization: Bearer <admin-token>" \
  -d '{"role": "readonly", "projects": ["proj-a", "proj-b"]}'

# Deactivate user (sessions revoked, record preserved)
curl -X DELETE http://localhost:7842/api/auth/users/alice \
  -H "Authorization: Bearer <admin-token>"
```

**Per-project scoping:** if `projects` is a non-empty list, the user can only run tasks against those project IDs. Admins always bypass scoping. Empty list = unrestricted.

### Audit Log

Every security event (login, logout, register, token refresh, API key create/revoke, OAuth login, task run, user update/deactivate) is appended to:

```
~/.config/orchid/audit/audit-YYYY-MM-DD.jsonl
```

Files rotate daily and are **never deleted**. Query via the API:

```bash
curl "http://localhost:7842/api/audit?limit=50&action=login_failed" \
  -H "Authorization: Bearer <admin-token>"
```

---

## Workflow Summary

**V2 planning workflow (new projects):**

```
1. orchid new "description"               create project with wizard
2. orchid --project PATH --discuss        discuss requirements with AI PM
3. orchid --project PATH --approve        generate REQUIREMENTS, ARCHITECTURE, tasks
4. orchid --project PATH --approve        advance to READY
5. orchid --project PATH --mode auto      execute tasks
6. orchid --project PATH --get-result T001   review output
7. Repeat: add tasks, approve, execute
```

**Direct task workflow (existing projects):**

```
1. orchid init PATH                       scaffold CLAUDE.md + tasks.md
2. Edit CLAUDE.md                         give the agent project context
3. Edit tasks.md                          describe what needs to be done
4. orchid --project PATH --status         confirm tasks are parsed correctly
5. orchid --project PATH --mode auto      let it run
6. Review output, mark done, add more tasks, repeat
```

---

## Common Issues

**"ANTHROPIC_API_KEY not set"**
Set it in `~/.config/orchid/.env`, or export it in your shell. Use `--offline` to skip Claude entirely.

**"Provider unavailable: local"**
llama.cpp is not running. Start it on port 8080, or add `model:claude` to tasks that need the API, or use `--offline` mode to confirm what's working.

**Task stays in TODO**
Check for unmet dependencies (`needs:` tags) or a `BLOCKED` status. Run `--status` to see the full board.

**Agent hits max iterations without finishing**
The task may be too broad. Break it into smaller atomic tasks — one clear deliverable per task ID works best.

**Phase won't advance**
A gate is waiting for human approval. Run `orchid --project PATH --approve`. To make a gate automatic, add `gates: {ready_to_executing: auto}` to `.orchid.yaml`.

**Discussion agent gives generic responses**
Fill in your machine profile at `~/.config/orchid/machine-profile.yaml` — preferred stacks, infrastructure, and project root. The discussion agent uses this to ask more targeted questions.

**Test agent uses wrong runner (e.g., bare `python -m pytest` in a Docker project)**
Set the environment explicitly in `.orchid.yaml`:
```yaml
agents:
  environment: docker   # docker | venv | node | python
```
Or use `verify_syntax_only: true` to skip test execution entirely and only check syntax.

**Agent not producing output / iterating too many times**
Run with `--trace` to see each ReAct iteration. This reveals whether the agent is miscalling tools, getting confused by tool output, or stuck in a reasoning loop.