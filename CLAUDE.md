<!-- compressed 2026-03-22 -->

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
orchid --project <path> --status|--recall "q"|--search "q"|--add-task "t"|--tail|--inject "text"|--get-result T001|--phase|--artifacts|--approve [--auto]
orchid init <path> [--name --description --force]
orchid decide "Title" --decision "..." --rationale "..." --project <path>
orchid new "<desc>" [--name NAME] [--dir PATH] [--type ai|web|tool|game]
orchid --multi --project <path-a> --project <path-b>
orchid telegram|slack|web --project <path> [--port 7842]  # DEPRECATED — use orchid serve --telegram/--slack
orchid serve [--watch-dir ~/LocalAI] [--project <path>] [--port 7842] [--telegram] [--slack] [--bots]
orchid --check-providers
```
tasks.md: `- [ ] **T003** Title \`type:code_generate\` \`p1\` \`needs:T001,T002\` \`model:claude\``
Rollup: `- [ ] **T099** Title \`type:rollup\` \`rollup:T090,T091\` \`output:FILE.md\``

## Tool Call Format (EXACT)
```
Action: read_file
Action Input: {"path": "src/server.js"}
```
One action per ReAct step. Actions: read_file / list_dir / bash / write_file / append_file / check_imports / get_task_files / delegate.

## File Writing Guidelines
- **append_file**: use when ADDING content to existing files (README, docs, etc.)
- **write_file**: use ONLY when explicitly replacing/rewriting entire file

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
- **D0036** Machine-level config at ~/.config/orchid/.env (XDG). load_dotenv() order: cwd → ~/.config/orchid/.env → ~/LocalAI/orchid/.env (legacy).
- **D0037** Rollup task: `type:rollup` `rollup:T001,T002` `output:FILE.md` — gathers TaskResultStore results, synthesises via Claude, writes output. Always uses Claude.
- **D0038** TaskResultStore: JSON Lines at `.orchid/task_results.json`. Appended on task completion. CLI: `--get-result T001`.
- **D0039** Shell tool dual-mode: `agents.shell_mode: blocklist` (default); `allowlist` restricts bash to ~40 known-safe executables plus project additions via `agents.shell_allowlist`. Blocklist always runs first.
- **D0040** Vector memory chunking uses tiktoken `cl100k_base` BPE token counting; falls back to `len//3` char estimate if absent. `chunk_size` config key is token limit, not word count.
- **D0041** V2 lifecycle state machine (orchid/lifecycle.py): NEW → DISCUSSING → REQUIREMENTS → PLANNING → READY → EXECUTING → COMPLETE. Any phase can return to DISCUSSING. Persisted at `<project>/.orchid/project.state.json`.
- **D0042** Strategic agent tier: DiscussionAgent elicits requirements; ProductManagerAgent generates REQUIREMENTS.md + ARCHITECTURE.md; ProjectManagerAgent generates MILESTONES.md + tasks.md. All use provider registry (default: claude).
- **D0043** Gate system (orchid/gates.py): human|auto gates control phase transitions. Human gates require `orchid --approve`. Config: gates.default + per-transition overrides in defaults.yaml and .orchid.yaml lifecycle.gates.
- **D0044** Machine profile (orchid/machine_profile.py): developer preferences at ~/.config/orchid/machine-profile.yaml. Supplies project_root, preferred_stacks, infrastructure, defaults. Injected into strategic agent prompts.
- **D0045** Web UI Planning tab: 9 REST + 1 WS endpoints. React components: PlanningTab, PhaseIndicator, DiscussionPanel, ArtifactPanel, ApprovalPanel, NewProjectWizard. Settings tab with machine profile editor and provider status.
- **D0046** Discussion streaming via WebSocket `/ws/{id}/discussion`: `{type:"thinking"}` → `{type:"token", data:full_response}` → `{type:"done", data:metadata}`. Main WS broadcasts `advance_status`/`advance_artifact`/`advance_done`.
- **D0047** NewProjectWizard: 4-step modal (Name+Description → Confirm Path → Options → Creating). Auto-slugifies name. POST /api/projects with confirm_path:false before confirm_path:true.
- **D0048** Prompt caching: AnthropicProvider (`supports_explicit_caching=True`) auto-caches system prompts ≥2048 chars via `cache_control:{type:ephemeral}`; `cacheable_prefix=N` caches first N messages. DiscussionAgent splits static instructions (cached) from stable context+history (cacheable). PM/PMgr pass `cacheable_prefix=1`. LocalProvider/OllamaProvider (`supports_implicit_caching=True`) send `cache_prompt:true` and use `optimize_for_caching()`. Session cache stats logged at close. Config at `caching:` in orchid.defaults.yaml.
- **D0049** Local KV cache hit detection uses relative ms/tok threshold (<1.0ms/token = cache hit) with rolling average tracking per model, not absolute tok/ms.
- **D0050** Central bot architecture: single `CentralBotManager` in `orchid/interfaces/central_bot.py` manages both Telegram and Slack bots. Started as part of `orchid serve --telegram/--slack/--bots`. Replaces project-scoped bots. Discovery callbacks wire new/removed projects to both bots.
- **D0051** Telegram user state at `~/.config/orchid/telegram-state.json`: `{user_id: {active_project, active_project_path, phase, last_interaction}}`. Written atomically. Commands prefixed `/orchid_*` (underscores — Telegram requires `[a-z0-9_]`). Per-user active project context; `/orchid_switch` to change.
- **D0052** Slack channel map at `~/.config/orchid/slack-channels.json`: `{channel_id: project_path}`. Auto-creates `#{name}-project` channel on project discovery. `/orchid-add-channel --project <name>` links any existing channel. Global commands in `#orchid-general`; project commands auto-routed by channel.
- **D0053** `orchid serve --telegram` / `--slack` / `--bots` flags enable the central bots as part of the serve process. Old `orchid telegram` / `orchid slack` subcommands show deprecation warnings and suggest the new flags. Requires `--watch-dir` for discovery.

## Current State
**413 tests passing.** Completed through T060.
- T051: shell allowlist + BPE chunking
- T053: V2 lifecycle + strategic agents + CLI
- T054: Planning tab API + React; DDG test marked skipif `ORCHID_NETWORK_TESTS!=true`
- T055: DiscussionPanel loading/streaming UX; local KV cache hit detection fix (D0049)
- T056: Prompt caching (D0048) + 17 tests; wrote V2-SUMMARY.md
- T057: Added Orchid V2 one-line description to README.md
- T058: Code review of anthropic.py prompt caching — **PASS WITH RESERVATIONS** (3 bugs identified)
- T059: Review prompt caching in orchid/providers/anthropic.py — cache_control blocks correctly applied
- T060: Added File Writing Guidelines to CLAUDE.md and system prompt (append_file vs write_file)
- T061: Central bot architecture — CentralBotManager, CentralTelegramBot, CentralSlackBot, serve --bots, 15 tests

**Orchid V2**: Two-tier agent routing (Claude + local LLM), lifecycle-based planning phases, real-time web UI with task board.

## Install
```bash
uv venv && uv pip install -e ".[dev]"
# ANTHROPIC_API_KEY required; llama.cpp: localhost:8080
cp .env ~/.config/orchid/.env && chmod 600 ~/.config/orchid/.env
```