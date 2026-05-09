# Orchid Development Context

**Version:** 2.2.4 | **License:** AGPL-3.0 | **State:** V2.2 Complete (Agentic OS gap-closure done) | **Tests:** 1207+ passing

> Use this document to orient a new developer — or a new Claude conversation — without requiring prior chat history. Paste it as context, then describe the specific task.

---

## 1. Project Identity

Orchid is a standalone AI agent orchestration framework that runs autonomously on developer machines. It reads a project's `tasks.md` and `CLAUDE.md`, routes each task to the appropriate AI provider (Claude API or a local llama.cpp-compatible model), and executes a ReAct (Reason → Act → Observe) loop to complete software development, research, drafting, and review tasks. Projects opt-in by placing `.orchid.yaml`, `CLAUDE.md`, and `tasks.md` in their root. Orchid itself lives at `~/LocalAI/orchid/` and is distinct from the external projects it manages (`~/projects/<name>/`). V2 added a full project lifecycle state machine (NEW → DISCUSSING → REQUIREMENTS → PLANNING → READY → EXECUTING → COMPLETE), strategic AI agents (ProductManager, ProjectManager, DiscussionAgent), a React web dashboard, and Telegram/Slack bots. V2.1 added TesterAgent, PM Dashboard, task metrics, Discussion history, active/inactive project grouping, and per-agent provider overrides. V2.2 closed all 19 Agentic OS gaps (subprocess isolation, cancellation, watchdog, cycle detection, file locks, mid-task checkpointing, agent mailbox, max-iterations cap, capability registry, remote workers, auth layer, container isolation, distributed cost ledger, checkpoint export).

**Repository:** `/home/dave/LocalAI/orchid`
**Install:** `uv venv && uv pip install -e ".[dev]"` (dev) or `uv tool install . --force` (global CLI)
**Env config:** `~/.config/orchid/.env` (requires `ANTHROPIC_API_KEY`)
**Service URL:** `http://localhost:7842` (React UI + REST API + WebSocket)
**Systemd service:** `/etc/systemd/system/orchid-serve.service`
**Service source template:** `scripts/orchid-serve.service`

---

## 2. Architecture Overview

### Key Files (one line each)

| File | Role |
|------|------|
| `orchid/interfaces/cli.py` | Typer CLI entry point — all `orchid` commands |
| `orchid/orchestrator.py` | Main task loop, ReAct dispatch, TraceWriter |
| `orchid/session.py` | Hot memory (CLAUDE.md), session log, compression |
| `orchid/lifecycle.py` | Phase state machine, `ProjectLifecycle`, `ProjectState` |
| `orchid/gates.py` | Gate validation logic for phase advancement |
| `orchid/planning.py` | `PlanningSession` — orchestrates Discussion → artifacts |
| `orchid/agents/base.py` | `BaseAgent` with ReAct loop, built-in tools, delegation |
| `orchid/agents/developer.py` | Code generation agent |
| `orchid/agents/reviewer.py` | Code review agent |
| `orchid/agents/researcher.py` | Web search + summarise agent |
| `orchid/agents/tester.py` | `TesterAgent` — environment detection, syntax + runtime tests |
| `orchid/agents/discussion_agent.py` | AI discussion facilitator (V2) |
| `orchid/agents/product_manager.py` | Generates REQUIREMENTS.md + ARCHITECTURE.md |
| `orchid/agents/project_manager.py` | Breaks requirements into milestones + tasks.md |
| `orchid/agents/pm_agent.py` | PM planning orchestration |
| `orchid/agent_manager.py` | Per-project run state, APScheduler cron scheduling |
| `orchid/providers/registry.py` | `ProviderRegistry`, 7-layer resolution |
| `orchid/providers/anthropic.py` | Anthropic API with prompt caching + retry |
| `orchid/providers/local.py` | OpenAI-compat (llama.cpp, LM Studio, etc.) |
| `orchid/providers/ollama.py` | Ollama native API |
| `orchid/providers/openai.py` | OpenAI + OpenRouter providers |
| `orchid/providers/bedrock.py` | AWS Bedrock provider |
| `orchid/interfaces/web_server.py` | FastAPI app — REST API, WebSocket, React SPA |
| `orchid/interfaces/central_bot.py` | `CentralBotManager` — multi-project bot hub |
| `orchid/interfaces/telegram_central.py` | Central Telegram bot (D0050) |
| `orchid/interfaces/slack_central.py` | Central Slack bot with Socket Mode (D0024) |
| `orchid/memory/vector.py` | ChromaDB vector store with tiktoken chunking |
| `orchid/memory/decisions.py` | Architecture decisions log (decisions.json) |
| `orchid/memory/state.py` | `TaskResultStore`, `Task`, `TaskStatus` |
| `orchid/tools/filesystem.py` | `read_file`, `write_file`, `append_file`, `list_dir` with post-write syntax verification |
| `orchid/tools/shell.py` | `bash` tool with allowlist/blocklist enforcement |
| `orchid/tools/consistency.py` | `check_imports` — broken import detection |
| `orchid/tools/search.py` | SearXNG / Brave / DuckDuckGo web search |
| `orchid/config.py` | 3-layer config merge: orchid.defaults.yaml → .orchid.yaml → env |
| `orchid/discovery.py` | Watchdog-based project auto-discovery |
| `orchid/runner.py` | `BackgroundRunner` — thread-pool agent execution for web server |
| `orchid/multi.py` | Multi-project parallel runner |
| `orchid/discussion.py` | Discussion history persistence |
| `orchid/machine_profile.py` | Machine capability detection |
| `orchid/orchid.defaults.yaml` | Authoritative default config (models, routing, memory, agents, isolation, remote) |
| `orchid/interfaces/web_ui/` | React frontend (Vite, `src/components/`) |
| `orchid/subprocess_runner.py` | `SubprocessRunner` — child-process task isolation, stdin JSON / stdout NDJSON |
| `orchid/worker_protocol.py` | `TaskContext`, `WorkerEvent`, `WorkerResult` dataclasses (subprocess IPC) |
| `orchid/watchdog.py` | `TaskWatchdog` daemon thread — fires task.stuck hook after stall threshold |
| `orchid/locks.py` | `FileLockRegistry` — per-path threading.Lock for parallel write safety |
| `orchid/mailbox.py` | `AgentMailbox` — thread-safe per-agent message queue; get/drop singletons |
| `orchid/capability.py` | `CAPABILITY_REGISTRY` + `AgentCapability` dataclass; orchestrator validates at spawn |
| `orchid/container_runner.py` | `ContainerRunner` — Docker-based isolation with graceful fallback |
| `orchid/remote/types.py` | `WorkerNode`, `RemoteTaskRequest`, `RemoteTaskResponse` dataclasses |
| `orchid/remote/worker_server.py` | FastAPI worker node server (/health, /task, /ledger, port 8001) |
| `orchid/remote/dispatcher.py` | `RemoteDispatcher` — node selection, dispatch with retry, ledger merge |
| `orchid/auth/types.py` | `User`, `AuthError` dataclasses |
| `orchid/auth/store.py` | `UserStore` — JSON-backed user registry, all fields persisted |
| `orchid/auth/middleware.py` | `AuthMiddleware` — token validation, `get_current_user` dependency |

### Provider Resolution Order (7 layers, highest to lowest)

```
1. CLI --provider flag                         (agent=provider syntax, repeatable)
2. Project .orchid.yaml providers.<agent_name>  (e.g. providers.discussion: local)
3. Project .orchid.yaml providers.task_types.<type>
4. Task model: annotation in tasks.md          (model:claude | model:local)
5. Env var ORCHID_<AGENT_TYPE>_PROVIDER
5b. Keyword-heuristic escalation               (routing.escalation config)
6. Config task-type defaults                   (providers.task_type_defaults in orchid.defaults.yaml)
7. Config agent-type defaults                  (providers.agent_defaults in orchid.defaults.yaml)
   → hardcoded Python fallback if all else absent
```

**Offline mode** (`--offline`) overrides everything to `local`.

Default routing by task type:
- **Claude**: `orchestrate`, `review`, `critique`, `plan`, `synthesize`, `rollup`
- **Local**: `draft`, `code_generate`, `summarize`, `search`, `transform`, `research`

Default routing by agent type:
- **Claude**: `orchestrator`, `reviewer`, `discussion`, `product_manager`, `project_manager`
- **Local**: `developer`, `researcher`, `base`

### ReAct Loop

Each agent iterates up to `max_iterations` (default: 50) cycles of:

```
Thought: <reasoning>
Action: <tool_name>
Action Input: <json>
--- framework executes tool ---
Observation: <result>
```

Available tools: `read_file`, `write_file`, `append_file`, `list_dir`, `bash`, `check_imports`, `delegate`. After each `write_file` on `.py`/`.js` files, syntax verification runs automatically. The `delegate` action spawns a sub-agent (max depth 3, max 5 sub-iterations).

When the agent is satisfied: `Final Answer: <text>` terminates the loop.

### V2 Lifecycle Phases

```
NEW → DISCUSSING → REQUIREMENTS → PLANNING → READY → EXECUTING → COMPLETE
```

Any phase can also return to `DISCUSSING`. Each transition requires a gate check. State persisted at `<project>/.orchid/project.state.json`.

- **NEW**: Project initialised, no discussion started
- **DISCUSSING**: DiscussionAgent active, refining requirements with user
- **REQUIREMENTS**: ProductManagerAgent has generated `REQUIREMENTS.md` + `ARCHITECTURE.md`
- **PLANNING**: ProjectManagerAgent has generated milestones + `tasks.md`
- **READY**: Plan approved, awaiting execution trigger
- **EXECUTING**: Orchestrator running tasks
- **COMPLETE**: All tasks done

### Two-Tier Routing

Claude (API) handles high-complexity work: orchestration, code review, planning, synthesis. Local model (llama.cpp-compatible) handles bulk work: code generation, drafting, research, search. The `routing.escalation` keywords in `orchid.defaults.yaml` can automatically promote a local-routed task to Claude when the title contains complexity signals.

---

## 3. Current State

### Completed Features

- **V1 core**: ReAct loop, session compression, hot memory (CLAUDE.md), decisions log, vector memory (ChromaDB), web search, delegation, task dependencies, task archiving
- **V2 lifecycle**: Phase state machine, gates, Discussion → Requirements → Planning → Executing → Complete
- **V2 strategic agents**: DiscussionAgent, ProductManagerAgent, ProjectManagerAgent
- **V2 Web UI**: FastAPI + React dashboard (port 7842), task board, session stream, hot memory view, recall search, planning tab, discussion panel, artifact panels (Requirements/Architecture/Milestones/Tasks), approval panel, phase indicator, new-project wizard
- **V2 PM Dashboard**: MilestoneProgress, DependencyGraph, SessionBurndown, PhaseTimeline, TaskTiming — all read-only
- **V2.1 features**:
  - TesterAgent (`orchid/agents/tester.py`) — environment-aware test runner
  - Auto-verify task injection (post code_generate)
  - Task metrics capture (`.orchid/task_metrics.jsonl`, `/api/projects/{id}/metrics`)
  - `verify_syntax_only` mode (`.orchid.yaml agents.verify_syntax_only: true`)
  - Per-agent provider overrides in `.orchid.yaml`
  - Active/Inactive project grouping (Web UI + bots)
  - SKIP task status (`[~]` in tasks.md, `orchid task skip --id T015`)
  - `--run-task T001` CLI flag + Web UI ▶ Run button
  - Discussion history tab in Planning UI
  - Project Config tab (read-only `.orchid.yaml` + `.env`)
  - `orchid task skip` subcommand
  - CentralBotManager (`orchid serve --bots/--telegram/--slack`)
  - XDG config migration (`~/.config/orchid/.env`)
  - Shell allowlist/blocklist enforcement
  - BPE/tiktoken chunking (hard cap 800 tokens)
  - Exponential backoff on 429 (max 3 retries, 60s)
  - Prompt caching (D0048) in AnthropicProvider
  - Post-write syntax verification in `write_file`
  - `check_imports` tool auto-called by Reviewer
  - Task file manifests (files_created/files_modified in session log)
  - Offline mode: hot memory compression uses local model
  - `--run-task` ignores queue order, runs specific task
  - Task metrics: iters_used, duration_s, action counts, blocker details
  - scripts/deploy.sh (build frontend → reinstall → restart service → tail logs)
  - venv/Docker environment detection for agents
  - PM Guide (`docs/pm-guide.md`) including fully-local operation section
  - Discussion panel focus fixes (auto-refocus after AI response)
  - Planning tab scroll fixes (min-height:0, overflow chain)
  - Phase indicator shows correct "can advance to" (filters current phase)
  - Slack channel unlink command (`/orchid-unlink-channel`)
  - `/orchid-projects`, `/orchid-switch`, `/orchid-approve` Telegram commands
  - systemd service + install script

- **V2.2 Agentic OS gap-closure (T209–T284, completed 2026-05-08):**
  - Subprocess isolation: `SubprocessRunner` + `worker_protocol.py` (stdin JSON / stdout NDJSON)
  - Cancellation: `AgentCancelledError` + `cancel_event` threading.Event through ReAct loop
  - Wall-clock timeout: orchestrator timer fires `agent.cancel()` after `max_task_seconds`
  - Stuck-task watchdog: `TaskWatchdog` daemon fires `task.stuck` hook + marks BLOCKED
  - Cycle detection: `DependencyGraph.has_cycle()` checked after every `spawn_task()`
  - File advisory locks: `FileLockRegistry` in `locks.py`; wired into `write_file`/`append_file`
  - Mid-task ReAct checkpoint: `ReActCheckpoint` saved every 5 iterations; resume on crash
  - Agent mailbox IPC: `AgentMailbox` + `send_message`/`receive_message` ReAct tools
  - Shell agent-ID: `agent_id` param in `bash()` for shell-layer identity tracking
  - Max-iterations hard cap: `agents.max_iterations` config + `MaxIterationsError` in `BaseAgent`
  - Capability registry: `CAPABILITY_REGISTRY` + `AgentCapability` in `capability.py`
  - Remote workers: `orchid/remote/` (types, worker_server, dispatcher) + `orchid worker --port 8001`
  - Distributed cost ledger: `node_id` field + `merge_from_file()` in `cost/ledger.py`
  - Auth layer: `orchid/auth/` (types, store, middleware); per-user budget in `CostScheduler`
  - Container isolation: `ContainerRunner` (Docker, graceful fallback to subprocess)
  - File write audit: `write_file`/`append_file` entries in `audit_log.jsonl`
  - Checkpoint export: `export_checkpoint()` in `checkpoint/restore.py`
  - Config validation: `orchid.defaults.yaml` updated with `isolation`, `remote`, `agents.max_iterations` blocks

**Test count:** 1207+ passing (1207 collected, 8 pre-existing failures: 6 cost ledger patching, 2 SearXNG live network tests)

### Active Integrations (known working)
- **Telegram**: Central multi-project bot via `orchid serve --telegram`
- **Slack**: Socket Mode central bot via `orchid serve --slack`
- **Web UI**: React SPA at `http://localhost:7842`
- **Local model**: llama.cpp-compatible via `LLAMA_BASE_URL`
- **Claude API**: `ANTHROPIC_API_KEY`

---

## 4. Key File Locations

| Resource | Path |
|----------|------|
| Main env config | `~/.config/orchid/.env` |
| Machine profile | `~/.config/orchid/machine-profile.yaml` |
| Telegram state | `~/.config/orchid/telegram-state.json` |
| Slack channel map | `~/.config/orchid/slack-channels.json` |
| Default config | `orchid/orchid.defaults.yaml` |
| systemd service (source) | `scripts/orchid-serve.service` |
| systemd service (installed) | `/etc/systemd/system/orchid-serve.service` |
| Deploy script | `scripts/deploy.sh` |
| React UI source | `orchid/interfaces/web_ui/src/` |
| React UI build output | `orchid/interfaces/web_ui/dist/` |
| Per-project state | `<project>/.orchid/project.state.json` |
| Per-project decisions | `<project>/.orchid/decisions.json` |
| Per-project task results | `<project>/.orchid/task_results.json` |
| Per-project task metrics | `<project>/.orchid/task_metrics.jsonl` |
| Per-project session logs | `<project>/.orchid/session_logs/` |
| Per-project trace | `<project>/.orchid/trace.log` |
| Per-project vector DB | `<project>/.orchid/chroma/` |
| Hot memory | `<project>/CLAUDE.md` |
| Task board | `<project>/tasks.md` |
| Project config | `<project>/.orchid.yaml` |

---

## 5. Development Patterns

### How to implement new features

- **Code changes**: Use Claude Code directly (the current tool). Orchid's own development uses Claude Code for all code tasks.
- **Docs/drafts**: Use `orchid --project . --mode auto` offline (local model) for bulk documentation.
- **Test first**: Add tests in `tests/test_<feature>.py`. Run with `python3 -m pytest tests/test_<feature>.py -v`.
- **Full suite**: `python3 -m pytest tests/ -q --tb=short` (expect 500+ passing).

### How to add new CLI flags

1. Add `typer.Option(...)` parameter to `main()` in `orchid/interfaces/cli.py:81`
2. Add to `_any_project_command` tuple if it requires a project (line ~177)
3. Add dispatch logic in `main()` body or as a new subcommand via `@app.command()`
4. Update `CLAUDE.md` CLI reference section

### How to add new Web UI components

1. Create `orchid/interfaces/web_ui/src/components/<ComponentName>.jsx`
2. Import + render in `App.jsx` or relevant parent component
3. Wire to existing WebSocket connection (`useWebSocket` hook in `src/hooks/`)
4. Add API endpoint if needed (see next section)
5. Rebuild: `cd orchid/interfaces/web_ui && npm run build`

### How to add new API endpoints

1. Add handler function in `orchid/interfaces/web_server.py` inside `create_app()`
2. Follow pattern: `@app.get("/api/projects/{project_id}/<resource>")` with `async def`
3. Use `_projects[project_id]` to get the absolute project path
4. Raise `HTTPException(404, "project not found")` if `project_id not in _projects`
5. Return a dict (FastAPI auto-serialises to JSON)

Full API surface (current):
```
GET  /health
GET  /api/version
GET  /api/providers
GET  /api/projects
POST /api/projects
GET  /api/projects/{id}/status
GET  /api/projects/{id}/tasks
POST /api/projects/{id}/tasks
GET  /api/projects/{id}/decisions
GET  /api/projects/{id}/sessions
GET  /api/projects/{id}/sessions/{session_id}
GET  /api/projects/{id}/metrics
POST /api/projects/{id}/recall
POST /api/projects/{id}/search
POST /api/projects/{id}/run
DELETE /api/projects/{id}/run
GET  /api/projects/{id}/run/status
POST /api/projects/{id}/tasks/{task_id}/run
GET  /api/projects/{id}/settings
POST /api/projects/{id}/inject
GET  /api/projects/{id}/lifecycle
GET  /api/projects/{id}/discussion
POST /api/projects/{id}/discussion
POST /api/projects/{id}/advance
POST /api/projects/{id}/approve
GET  /api/projects/{id}/artifacts
GET  /api/machine-profile
PUT  /api/machine-profile
GET  /api/discovery
WS   /ws/{project_id}
WS   /ws/{project_id}/discussion
```

### Task type routing rules

Define task type in `tasks.md` with backtick annotation:
```markdown
- [ ] **T101** Title `type:code_generate` `p1` `needs:T100` `model:claude`
```

Valid types and their default provider: see `orchid/providers/registry.py:_TASK_TYPE_DEFAULTS` and the routing section above. Use `model:claude` override for complex code tasks that default to local.

### Test patterns

- Tests live in `tests/`
- Fixtures in `tests/conftest.py` (if present) or inline
- Use `tmp_path` pytest fixture for project dirs
- Mock providers with `unittest.mock.patch`
- Integration tests use real filesystem, no mocks on DB/files
- Run single file: `python3 -m pytest tests/test_lifecycle.py -v`
- Run with output: `python3 -m pytest tests/test_lifecycle.py -v -s`

---

## 6. Current Backlog

**All tasks in `tasks.md` are currently complete.** The task board as of 2026-04-08 shows no `[ ]` (TODO) or `[~]` (SKIP) items — everything from T001 through T091 is marked `[x]` (done).

To add new work:
```bash
orchid --project ~/LocalAI/orchid --add-task "Description" --type code_generate --priority 1
# or directly edit tasks.md
```

---

## 7. Known Issues / Gotchas

**Test collection errors (3 files):**
- `tests/test_cli_decide.py` — collection error, unknown root cause
- `tests/test_init.py` — collection error
- `tests/test_metrics.py` — collection error
- Workaround: `pytest tests/ --ignore=tests/test_cli_decide.py --ignore=tests/test_init.py --ignore=tests/test_metrics.py`

**`verify_syntax_only` for Docker projects:**
When a project has a Docker environment but the daemon isn't running, agents will waste iterations on failed runtime tests. Set `agents.verify_syntax_only: true` in `.orchid.yaml` to limit to `py_compile` / `node --check` only.

**`max_iterations` default is 50:**
Was 10, then 25, now 50 (set in `orchid.defaults.yaml agents.max_iterations`). Complex tasks rarely need more; if they hit the limit, the task is marked `BLOCKED` with a `max_iterations_reached` reason.

**ProjectManager can truncate task titles:**
Validation was added, but monitor for truncated `**T0XX**` entries in generated `tasks.md`. If titles are cut off mid-sentence the task parser may fail to extract them.

**Discussion panel focus quirks:**
After AI response, input is auto-refocused (`inputRef.current?.focus()`). If clicking numbered options doesn't focus, check that the `onClick` handler on option buttons still calls `focus()` after setting input value. This was fixed in T088 but could regress.

**Slack slash commands must be registered:**
Slash commands (`/orchid-status`, `/orchid-projects`, `/orchid-switch`, `/orchid-approve`, `/orchid-unlink-channel`) must be registered at `api.slack.com/apps` under your app's Slash Commands section with the correct Request URL pointing to your Orchid server. They won't appear or work until registered.

**Prompt caching (D0048):**
`cache_control` blocks are injected on the system prompt and first large user message. Cache hits are detected heuristically by `ms/tok` ratio (`< 1.0 ms/token`). Rolling average calibration per model improves accuracy over time.

**Offline mode hot memory:**
When `--offline` is set, session compression uses the local provider. Without this fix (T033), it would call the Claude API even in offline mode.

**Provider registry singleton:**
`get_registry()` returns a module-level singleton. After config changes in tests, call `reset_registry()` to force a reload. Otherwise stale config bleeds between tests.

**React build required after UI changes:**
The FastAPI server serves `orchid/interfaces/web_ui/dist/`. Changes to `.jsx` files have no effect until `npm run build` is re-run inside `orchid/interfaces/web_ui/`.

---

## 8. Architecture Decisions Reference

D0001–D0010 (foundations):
- **D0001** File-state — project state in files (tasks.md, CLAUDE.md), not a database
- **D0002** 2-tier routing — Claude for reasoning, local for bulk work
- **D0003** ReAct text — text-based Thought/Action/Observation loop (not function calling)
- **D0004** Interface-agnostic — CLI, Telegram, Slack, Web all share the same runner
- **D0005** 3-layer config — orchid.defaults.yaml → .orchid.yaml → env vars
- **D0006** Standalone runtime — Orchid manages external projects, not vice versa
- **D0007** Embed Chroma — embedded ChromaDB, no separate process
- **D0008** Embed: llama→ST — local embedding via llama.cpp or SentenceTransformers fallback
- **D0009** Auto-embed/recall — auto-embed session results, auto-recall at task start
- **D0010** Search: SearXNG→Brave — cascading web search backends

D0011–D0020:
- **D0011** Extract: trafilatura — web page content extraction
- **D0012** Delegate depth 3 — max 3 levels of agent delegation
- **D0013** Sub-context — delegated agents get sub-context not full parent context
- **D0014** Telegram logic — per-project Telegram bot mode (legacy, see D0050)
- **D0015** User whitelist — Telegram/Slack allowed_users list
- **D0016** Model routing — per-task model selection via task type + annotations
- **D0017** Task deps — `needs:T002` syntax, dependent tasks blocked until parent done
- **D0018** Live log — streaming session log to `--tail` and WebSocket
- **D0019** Inject queue — `inject.queue` file for injecting context into running agent
- **D0020** Telegram notify — task completion notifications to Telegram

D0021–D0030:
- **D0021** Process parallelism — multi-project runs in separate processes
- **D0022** Claude semaphore — max 1 concurrent Claude call to avoid rate limits
- **D0024** Slack Socket — Slack bot uses Socket Mode (no public URL needed)
- **D0025** Slack threads — replies go in thread of original message
- **D0026** Shared Runner — BackgroundRunner shared across web server + agent manager
- **D0027** Web FastAPI/React — FastAPI backend + React frontend
- **D0028** React dist — React built to `dist/`, served as static files
- **D0029** Traefik TLS — optional Traefik reverse proxy for HTTPS
- **D0030** ProviderBase ABC — abstract base class; resolution order documented above ⭐

D0031–D0053:
- **D0031** Shared backends — providers are module-level singletons
- **D0032** Provider check — `orchid --check-providers` probes all configured providers
- **D0033** Watchdog — inotify-based project auto-discovery
- **D0034** Orchid serve — unified `orchid serve` replaces separate web/telegram/slack commands ⭐
- **D0035** AgentManager — per-project run state + APScheduler cron
- **D0036** XDG config — config at `~/.config/orchid/` (XDG standard)
- **D0037** Rollup Claude — rollup tasks default to Claude but can be overridden via providers.rollup in .orchid.yaml
- **D0038** TaskResultStore — structured task results in `.orchid/task_results.json`
- **D0039** Shell allowlist — bash tool uses configurable allowlist/blocklist
- **D0040** Tiktoken chunking — BPE-based chunking, hard cap 800 tokens
- **D0041** V2 Lifecycle — phase state machine, gates, lifecycle file ⭐
- **D0042** Strategic agents — DiscussionAgent, ProductManagerAgent, ProjectManagerAgent ⭐
- **D0043** Gates — gate validation before phase advancement
- **D0044** Machine profile — machine capability detection for routing hints
- **D0045** Web Planning — Planning tab in Web UI
- **D0046** WS Stream — WebSocket streaming for discussion and agent events
- **D0047** Wizard — New Project Wizard in Web UI
- **D0048** Prompt cache — `cache_control` blocks in AnthropicProvider
- **D0049** KV cache — local KV cache hit detection (ms/tok heuristic)
- **D0050** CentralBot — single `CentralBotManager` serves all projects ⭐
- **D0051** Telegram state — `telegram-state.json` tracks per-user active project
- **D0052** Slack map — `slack-channels.json` maps channels to projects
- **D0053** Bot serve — `orchid serve --bots/--telegram/--slack` flags

⭐ = most important for new contributors to understand

---

## 9. How to Use This Document

### Pasting into a new Claude conversation

1. Copy this entire file
2. Start a new conversation
3. Paste as your first message
4. Add a second message describing the specific task

Alternatively, provide just the relevant sections (e.g. §2 for architecture questions, §5 for implementation guidance, §7 for debugging).

### What context to add

After pasting this document, append:

```
Current task: <describe what you want to do>
Recent changes: <mention any files you changed in the last session if relevant>
Blockers: <any errors or unexpected behaviour>
```

### Example opening prompt template

```
<paste DEVELOPMENT-CONTEXT.md here>

---

I'm working on the Orchid project. Current task: [T092] Add <feature>.

The feature should: <one paragraph description>

Relevant files I know about:
- orchid/interfaces/cli.py (need new flag)
- orchid/interfaces/web_server.py (need new endpoint)

Please start by reading those files and then propose an implementation plan.
```

---

## 10. Quick Command Reference

### Most common CLI commands

```bash
# Run all pending tasks in auto mode
orchid --project ~/projects/myapp --mode auto

# Run a single specific task
orchid --project ~/projects/myapp --run-task T015

# Add a task
orchid --project ~/projects/myapp --add-task "Build the login page" --type code_generate --priority 1

# Show task board + status
orchid --project ~/projects/myapp --status

# Show lifecycle phase
orchid --project ~/projects/myapp --phase

# Approve gate (advance phase)
orchid --project ~/projects/myapp --approve

# Start interactive discussion
orchid --project ~/projects/myapp --interactive

# Tail live agent log
orchid --project ~/projects/myapp --tail

# Recall from vector memory
orchid --project ~/projects/myapp --recall "authentication patterns"

# Check provider availability
orchid --check-providers

# Run offline (local model only)
orchid --project ~/projects/myapp --mode auto --offline

# Force Claude for specific agent type
orchid --project ~/projects/myapp --mode auto --provider developer=claude

# Skip a task
orchid task skip --id T015 --project ~/projects/myapp

# Record architecture decision
orchid decide "Use PostgreSQL" --decision "Need ACID guarantees for billing data"

# Initialise a new project
orchid init ~/projects/newapp

# Start unified server (auto-discovers projects)
orchid serve --watch-dir ~/LocalAI --watch-dir ~/projects --port 7842

# Start server with bots
orchid serve --watch-dir ~/LocalAI --bots

# Multi-project parallel run
orchid --project ~/projects/app1 --project ~/projects/app2 --multi
```

### Deploy sequence

```bash
# Build React frontend
cd ~/LocalAI/orchid/orchid/interfaces/web_ui
npm run build

# Reinstall CLI globally
cd ~/LocalAI/orchid
uv tool install . --force

# Restart service
sudo systemctl restart orchid-serve

# Tail logs to confirm clean startup
sudo journalctl -u orchid-serve -f -n 50
```

### Test commands

```bash
# Full suite (fast)
python3 -m pytest tests/ -q --tb=short

# Full suite excluding known-broken collectors
python3 -m pytest tests/ -q --tb=short \
  --ignore=tests/test_cli_decide.py \
  --ignore=tests/test_init.py \
  --ignore=tests/test_metrics.py

# Single test file
python3 -m pytest tests/test_lifecycle.py -v

# Single test with stdout
python3 -m pytest tests/test_lifecycle.py::test_phase_transition -v -s

# Only fast tests (skip network)
python3 -m pytest tests/ -q -m "not slow"
```

### Service management

```bash
sudo systemctl status orchid-serve
sudo systemctl restart orchid-serve
sudo systemctl stop orchid-serve
sudo journalctl -u orchid-serve -f          # live logs
sudo journalctl -u orchid-serve --since "1h ago"  # last hour
```
