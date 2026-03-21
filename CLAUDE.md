<!-- compressed 2026-03-19 -->

# CLAUDE.md — Orchid Framework (compressed)

## What It Is
Standalone AI agent orchestration tool. Installed globally, invoked against external project dirs. Projects opt in via CLAUDE.md + tasks.md + optional .orchid.yaml.

## Layout
```
~/orchid/          ← tool repo
~/projects/webtron/
  CLAUDE.md / tasks.md / .orchid.yaml
  .orchid/decisions.json, session_logs/, chroma/
```

## Key CLI
```bash
orchid --project <path> --mode auto|interactive [--code-model claude|local|auto] [--provider developer=ollama] [--offline]
orchid --project <path> --status|--recall "q"|--search "q"|--add-task "t" [--model claude|local|auto]
orchid --project <path> --tail | --inject "text" | --get-result T001
orchid init <path> [--name --description --force]
orchid decide "Title" --decision "..." --rationale "..." --project <path>
orchid --multi --project <path-a> --project <path-b>
orchid telegram|slack|web --project <path> [--port 7842]
orchid serve [--watch-dir ~/LocalAI] [--project <path>] [--port 7842]
orchid --check-providers
# V2 lifecycle commands
orchid new "<description>" [--name NAME] [--dir PATH] [--type ai|web|tool|game]
orchid --project <path> --interactive [--provider discussion=ollama]
orchid --project <path> --phase
orchid --project <path> --artifacts
orchid --project <path> --approve [--auto]
```
tasks.md: `- [ ] **T003** Title \`type:code_generate\` \`p1\` \`needs:T001,T002\` \`model:claude\``
Rollup: `- [ ] **T099** Title \`type:rollup\` \`p1\` \`rollup:T090,T091\` \`output:FILE.md\``

## Architecture Decisions
- **D0001** File-based state (tasks.md, CLAUDE.md, decisions.json, session_logs). No DB.
- **D0002** Two-tier routing: Claude → orchestrate/review/plan/critique/synthesize; llama.cpp → draft/code_generate/summarize/search.
- **D0003** ReAct loop (Reason→Act→Observe), text-parsed. No function-calling API.
- **D0004** Interface-agnostic core; orchid/interfaces/ is thin layer.
- **D0005** Three-layer config: orchid.defaults.yaml → .orchid.yaml → CLI flags.
- **D0006** Standalone runtime. Projects NOT subfolders of Orchid.
- **D0007** ChromaDB embedded at `<project>/.orchid/chroma/`. No server.
- **D0008** Embedding priority: llama.cpp `/v1/embeddings` (port 8081, nomic-embed-text) → sentence-transformers all-MiniLM-L6-v2. Never OpenAI.
- **D0009** Auto-embed session log on close; auto-recall top-k into `## Recalled Context` on load.
- **D0010** Search priority: SearXNG → Brave → DuckDuckGo. Auto-probes and caches.
- **D0011** Content extraction: trafilatura → BeautifulSoup. Truncated to `web_search.max_page_chars` (default 8000).
- **D0012** Delegation via ReAct action `delegate[agent_type | task]`. Max depth 3. Sub-agents: `max_sub_iterations=5`.
- **D0013** Sub-context: task + top-3 vector recall + 1000-char parent context slice. Never full parent ReAct trace.
- **D0014** Telegram: TelegramBot + BackgroundRunner + telegram_formatter. No business logic in bot.
- **D0015** User whitelist: TELEGRAM_ALLOWED_USERS. Unset = warn + accept all.
- **D0016** Multi-tier model routing: CLI flag → task `model:` → keyword heuristic → type default → RouteDecision.
- **D0017** Task dependencies via `needs:T001,T002`. Cycle detection warns+skips. Controlled by `dependencies.enabled`.
- **D0018** Live streaming log: `.live.log` → renamed `.log` on close. `--tail` tails it.
- **D0019** Mid-run injection via `.orchid/inject.queue`. Agent reads+clears each ReAct iteration.
- **D0020** Proactive Telegram notifications at session_start/task_start/task_complete/task_failed/session_complete. Configurable via `telegram.notify_on`.
- **D0021** Process-per-project parallelism. No shared mutable state.
- **D0022** Claude API rate limiting via multiprocessing.Semaphore. Configurable via `multi.max_concurrent_claude_calls`.
- **D0023** Notification routing via multiprocessing.Queue; multi_formatter tags `[project]` prefix.
- **D0024** Slack uses Socket Mode (slack-bolt) — no public URL required.
- **D0025** Thread-per-task in Slack.
- **D0026** BackgroundRunner shared between Telegram and Slack.
- **D0027** Web UI: FastAPI (REST + WebSocket) + React (Vite) at port 7842.
- **D0028** React frontend built to web_ui/dist/, served by FastAPI StaticFiles. Dev mode uses Vite proxy to :7842.
- **D0029** Traefik bare-metal routes orchid.scheidy.com → localhost:7842, TLS via cloudflare certResolver.
- **D0030** ProviderBase ABC with 60s availability cache; ProviderRegistry singleton with 5-layer resolution: CLI flag → project config → ORCHID_\<AGENT\>_PROVIDER env → task_type default → agent_type hardcoded fallback.
- **D0031** All provider backends (Anthropic, LocalLlama, Ollama, OpenAI, OpenRouter, Bedrock) share ProviderBase. boto3 lazy import for Bedrock.
- **D0032** `--check-providers` probes all configured providers. `--offline` routes all to local.
- **D0033** Auto-discovery via watchdog inotify: scans watch_dirs (depth 2) for .orchid.yaml, debounced 2s. Excludes .venv/node_modules/.git/__pycache__.
- **D0034** `orchid serve` — unified persistent entry point: web UI + auto-discovery + optional agent loops. systemd: scripts/orchid-serve.service.
- **D0035** AgentManager: per-project agent loop threads with APScheduler for cron-based auto runs. Per-project persistent config in .orchid.yaml `persistent:` section.
- **D0039** Shell tool dual-mode: `agents.shell_mode: blocklist` (default) keeps existing behaviour; `allowlist` mode restricts bash to ~40 known-safe executables (git, python, node, uv, pytest, ruff, make, etc.) plus project additions via `agents.shell_allowlist`. Blocklist patterns always run first regardless of mode.
- **D0040** Vector memory chunking uses tiktoken `cl100k_base` BPE token counting (transitive dep via chromadb) to bound chunk size precisely; falls back to `len//3` char estimate if tiktoken is absent. `chunk_size` config key is now a token limit, not a word count.
- **D0036** Machine-level config at ~/.config/orchid/.env (XDG). load_dotenv() order: cwd → ~/.config/orchid/.env → ~/LocalAI/orchid/.env (legacy).
- **D0037** Rollup task: `type:rollup` `rollup:T001,T002` `output:FILE.md` — gathers TaskResultStore results, synthesises via Claude, writes output. Always uses Claude.
- **D0038** TaskResultStore: JSON Lines at `.orchid/task_results.json`. Appended on task completion. CLI: `--get-result T001`.
- **D0041** V2 project lifecycle state machine (orchid/lifecycle.py): NEW → DISCUSSING → REQUIREMENTS → PLANNING → READY → EXECUTING → COMPLETE. Any phase can return to DISCUSSING. Persisted at `<project>/.orchid/project.state.json`.
- **D0042** Strategic agent tier (orchid/agents/discussion_agent.py, product_manager.py, project_manager.py): DiscussionAgent elicits requirements conversationally; ProductManagerAgent generates REQUIREMENTS.md + ARCHITECTURE.md; ProjectManagerAgent generates MILESTONES.md + tasks.md. All use provider registry (default: claude).
- **D0043** Gate system (orchid/gates.py): human|auto gates control phase transitions. Human gates require `orchid --approve`. Auto gates advance automatically. Config: gates.default + per-transition overrides in defaults.yaml and .orchid.yaml lifecycle.gates.
- **D0044** Machine profile (orchid/machine_profile.py): developer preferences at ~/.config/orchid/machine-profile.yaml. Supplies project_root, preferred_stacks, infrastructure, defaults. Injected into strategic agent prompts. Created with defaults if absent.
- **D0045** Web UI Planning tab: 9 REST + 1 WS endpoints in web_server.py for lifecycle, discussion, advance, approve, artifacts, project creation, machine profile. React components: PlanningTab, PhaseIndicator, DiscussionPanel, ArtifactPanel, ApprovalPanel, NewProjectWizard. Settings tab with machine profile editor and provider status.
- **D0046** Discussion streaming via WebSocket `/ws/{id}/discussion`: sends `{type:"thinking"}` → `{type:"token", data:full_response}` → `{type:"done", data:metadata}`. No true token streaming (providers use complete()). Main WS broadcasts `advance_status`/`advance_artifact`/`advance_done` during `/api/projects/{id}/advance`.
- **D0047** NewProjectWizard: 4-step modal (Name+Description → Confirm Path → Options → Creating). Auto-slugifies name. Fetches suggested path from POST /api/projects with confirm_path:false before final creation with confirm_path:true.

## Tool Call Format (EXACT)
```
Action: read_file
Action Input: {"path": "src/server.js"}
```
One action per ReAct step. Actions: read_file / list_dir / bash / write_file / check_imports / get_task_files / delegate.

## Task Status
| ID | Status | Notes |
|----|--------|-------|
| T007 | Done | DDG ad-result filter (y.js URLs) |
| T008 | Done | decisions.json JSON Lines parse error |
| T034 | Done | `orchid task done` no longer requires TITLE when --id provided |
| T047 | Done | Dead stub `orchid/cli.py` deleted; real CLI at `orchid/interfaces/cli.py` unaffected |
| T009–T032 | Done | chunking, log parser, dep tests, Slack formatter, Web UI, CLI --help |
| T035 | Done | AnthropicProvider: exponential backoff+jitter on 429/APIConnectionError/APITimeoutError, max 3 retries, up to 60s |
| T036 | Done | discovery.py: skip inotify for non-existent dirs; exclude .venv/node_modules/.git |
| T037 | Done | scripts/deploy.sh: builds React, reinstalls via uv tool, restarts orchid-serve |
| T038 | Done | POST /api/projects/{id}/run passes absolute filesystem path to BackgroundRunner |
| T039 | Done | --model flag added to --add-task CLI |
| T040 | Done | XDG config ~/.config/orchid/.env; scripts/setup-config.sh (chmod 600) |
| T041 | Done | Post-write verification: .py→py_compile, .js→node syntax check |
| T042 | Done | tools/consistency.py: check_imports() scans .js/.py; ReAct action added; reviewer auto-calls at session end |
| T043 | Done | auto_review.enabled=false, auto_review.after_n_tasks=3 in orchid.defaults.yaml |
| T044 | Done | project_context() reads package.json/pyproject.toml; injected at task start |
| T045 | Done | File manifest on task completion: files_created/files_modified in session log; get_task_files(task_id) added |
| T046 | Done | Rollup task type: TaskResultStore, orchestrator synthesis, --get-result CLI; 25 tests |
| T048 | Done | 253 tests passing, 0 failed, 2.18s |
| T049 | Done | Rollup written to HEALTH-REPORT.md |
| T050 | Done | Provider empty-choices guards (local/ollama/openai/bedrock); Bedrock ProviderError; Anthropic retry extended |
| T051 | Done | Shell allowlist mode (D0039); systemd ProtectSystem=strict hardening; BPE chunking (D0040) |
| T052 | Done | GitHub Actions CI (.github/workflows/ci.yml); 302 tests passing |
| T053 | Done | V2 Session 1: lifecycle.py, machine_profile.py, discussion.py, gates.py, project_creator.py, discussion_agent.py, product_manager.py, project_manager.py; orchid new / --interactive / --approve / --phase / --artifacts CLI; 377 tests passing |
| T054 | Done | V2 Session 2: Planning tab API (9 endpoints + WS), React components (PlanningTab, PhaseIndicator, DiscussionPanel, ArtifactPanel, ApprovalPanel, NewProjectWizard), Settings tab, index.css planning styles; 396 tests passing |

**Tests: 396 passing**

## Install
```bash
uv venv && uv pip install -e ".[dev]"
# ANTHROPIC_API_KEY required; llama.cpp: localhost:8080
cp .env ~/.config/orchid/.env && chmod 600 ~/.config/orchid/.env
```