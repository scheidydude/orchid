<!-- compressed 2026-03-24 -->

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
orchid --project <path> --status|--recall "q"|--search "q"|--add-task "t"|--tail|--inject "text"|--get-result T001|--phase|--artifacts|--approve [--auto]|--run-task T001
orchid init <path> [--name --description --force]
orchid decide "Title" --decision "..." --rationale "..." --project <path>
orchid new "<desc>" [--name NAME] [--dir PATH] [--type ai|web|tool|game]
orchid --multi --project <path-a> --project <path-b>
orchid serve [--watch-dir ~/LocalAI] [--project <path>] [--port 7842] [--telegram] [--slack] [--bots]
orchid --check-providers
# DEPRECATED: orchid telegram|slack|web → use orchid serve --telegram/--slack
```
tasks.md: `- [ ] **T003** Title \`type:code_generate\` \`p1\` \`needs:T001,T002\` \`model:claude\``
Skip: `- [~] **T003** Title` (excluded from auto run)
Rollup: `- [ ] **T099** Title \`type:rollup\` \`rollup:T090,T091\` \`output:FILE.md\``

## Tool Call Format (EXACT)
```
Action: read_file
Action Input: {"path": "src/server.js"}
```
One action per ReAct step. Actions: read_file / list_dir / bash / write_file / append_file / check_imports / get_task_files / delegate.

**File Writing:** `append_file` = ADDING to existing files. `write_file` = ONLY when replacing entire file.

## Architecture Decisions
- **D0001** File-based state. No DB.
- **D0002** Two-tier routing: Claude → orchestrate/review/plan/critique/synthesize; llama.cpp → draft/code_generate/summarize/search.
- **D0003** ReAct loop, text-parsed. No function-calling API.
- **D0004** Interface-agnostic core; orchid/interfaces/ is thin layer.
- **D0005** Three-layer config: orchid.defaults.yaml → .orchid.yaml → CLI flags.
- **D0006** Standalone runtime. Projects NOT subfolders of Orchid.
- **D0007** ChromaDB embedded at `<project>/.orchid/chroma/`. No server.
- **D0008** Embedding priority: llama.cpp `/v1/embeddings` (port 8081, nomic-embed-text) → sentence-transformers all-MiniLM-L6-v2. Never OpenAI.
- **D0009** Auto-embed session log on close; auto-recall top-k into `## Recalled Context` on load.
- **D0010** Search priority: SearXNG → Brave → DuckDuckGo. Auto-probes and caches.
- **D0011** Content extraction: trafilatura → BeautifulSoup. Truncated to `web_search.max_page_chars` (default 8000).
- **D0012** Delegation via `delegate[agent_type | task]`. Max depth 3. Sub-agents: `max_sub_iterations=5`.
- **D0013** Sub-context: task + top-3 vector recall + 1000-char parent context slice.
- **D0014** Telegram: TelegramBot + BackgroundRunner + telegram_formatter. No business logic in bot.
- **D0015** User whitelist: TELEGRAM_ALLOWED_USERS. Unset = warn + accept all.
- **D0016** Multi-tier model routing: CLI flag → task `model:` → keyword heuristic → type default → RouteDecision.
- **D0017** Task dependencies via `needs:T001,T002`. Cycle detection warns+skips.
- **D0018** Live streaming log: `.live.log` → renamed `.log` on close.
- **D0019** Mid-run injection via `.orchid/inject.queue`.
- **D0020** Proactive Telegram notifications at session_start/task_start/task_complete/task_failed/session_complete.
- **D0021** Process-per-project parallelism. No shared mutable state.
- **D0022** Claude API rate limiting via multiprocessing.Semaphore (`multi.max_concurrent_claude_calls`).
- **D0024** Slack uses Socket Mode (slack-bolt) — no public URL required.
- **D0025** Thread-per-task in Slack.
- **D0026** BackgroundRunner shared between Telegram and Slack.
- **D0027** Web UI: FastAPI (REST + WebSocket) + React (Vite) at port 7842.
- **D0028** React frontend built to web_ui/dist/, served by FastAPI StaticFiles.
- **D0029** Traefik routes orchid.scheidy.com → localhost:7842, TLS via cloudflare certResolver.
- **D0030** ProviderBase ABC with 60s availability cache; 5-layer resolution: CLI flag → project config → ORCHID_\<AGENT\>_PROVIDER env → task_type default → agent_type hardcoded fallback.
- **D0031** All provider backends (Anthropic, LocalLlama, Ollama, OpenAI, OpenRouter, Bedrock) share ProviderBase. boto3 lazy import for Bedrock.
- **D0032** `--check-providers` probes all configured providers. `--offline` routes all to local.
- **D0033** Auto-discovery via watchdog inotify: scans watch_dirs (depth 2) for .orchid.yaml, debounced 2s.
- **D0034** `orchid serve` — unified persistent entry point. systemd: scripts/orchid-serve.service.
- **D0035** AgentManager: per-project agent loop threads with APScheduler for cron-based auto runs.
- **D0036** Machine-level config at ~/.config/orchid/.env (XDG). load_dotenv() order: cwd → ~/.config/orchid/.env → ~/LocalAI/orchid/.env (legacy).
- **D0037** Rollup task: gathers TaskResultStore results, synthesises via Claude, writes output. Always uses Claude.
- **D0038** TaskResultStore: JSON Lines at `.orchid/task_results.json`. CLI: `--get-result T001`.
- **D0039** Shell tool dual-mode: `agents.shell_mode: blocklist` (default); `allowlist` restricts bash to ~40 known-safe executables.
- **D0040** Vector memory chunking uses tiktoken `cl100k_base` BPE; falls back to `len//3`. `chunk_size` = token limit.
- **D0041** V2 lifecycle (orchid/lifecycle.py): NEW → DISCUSSING → REQUIREMENTS → PLANNING → READY → EXECUTING → COMPLETE. Persisted at `<project>/.orchid/project.state.json`.
- **D0042** Strategic agents: DiscussionAgent → REQUIREMENTS.md + ARCHITECTURE.md; ProjectManagerAgent → MILESTONES.md + tasks.md.
- **D0043** Gate system (orchid/gates.py): human|auto gates control phase transitions. `orchid --approve`.
- **D0044** Machine profile at ~/.config/orchid/machine-profile.yaml. Injected into strategic agent prompts.
- **D0045** Web UI Planning tab: 9 REST + 1 WS endpoints. Components: PlanningTab, PhaseIndicator, DiscussionPanel, ArtifactPanel, ApprovalPanel, NewProjectWizard, Settings tab.
- **D0046** Discussion streaming via WebSocket `/ws/{id}/discussion`: thinking → token → done.
- **D0047** NewProjectWizard: 4-step modal. POST /api/projects with confirm_path:false before confirm_path:true.
- **D0048** Prompt caching: AnthropicProvider auto-caches system prompts ≥2048 chars via `cache_control:{type:ephemeral}`; `cacheable_prefix=N`. LocalProvider/OllamaProvider send `cache_prompt:true`. Session cache stats logged at close.
- **D0049** Local KV cache hit detection uses relative ms/tok threshold (<1.0ms/token = cache hit) with rolling average per model.
- **D0050** Central bot architecture: single `CentralBotManager` in `orchid/interfaces/central_bot.py` manages both Telegram and Slack. Started via `orchid serve --bots`.
- **D0051** Telegram user state at `~/.config/orchid/telegram-state.json`. Commands prefixed `/orchid_*`. Per-user active project; `/orchid_switch` to change.
- **D0052** Slack channel map at `~/.config/orchid/slack-channels.json`. Auto-creates `#{name}-project` on discovery. Global commands in `#orchid-general`.
- **D0053** `orchid serve --telegram/--slack/--bots` enable central bots. Old subcommands show deprecation warnings.

## .orchid.yaml — Active/Inactive Projects
```yaml
active: true   # false = project excluded from auto-discovery/runs
```

## Current State
**446+ tests passing. V2.1 complete.**

| Task | Summary |
|------|---------|
| T051 | Shell allowlist + BPE chunking |
| T053 | V2 lifecycle + strategic agents + CLI |
| T054 | Planning tab API + React |
| T055 | DiscussionPanel streaming UX; D0049 fix |
| T056 | Prompt caching (D0048) + 17 tests |
| T058–T059 | Code review anthropic.py prompt caching — PASS WITH RESERVATIONS |
| T060 | File Writing Guidelines added |
| T061 | CentralBotManager + 15 tests |
| T064 | Fix --log-level: add `.lower()` in web_server.py |
| T066 | README V2.1 central bot docs |
| T068 | scripts/orchid-serve.service.template |
| T077 | README + docs/getting-started.md: --run-task, [~] skip, active/inactive grouping, Project Config tab, Discussion history |
| T078 | CLAUDE.md updated to V2.1 current state |

## Install
```bash
uv venv && uv pip install -e ".[dev]"
# ANTHROPIC_API_KEY required; llama.cpp: localhost:8080
cp .env ~/.config/orchid/.env && chmod 600 ~/.config/orchid/.env
```