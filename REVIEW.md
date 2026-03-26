# Orchid — Code & Application Review

**Date:** 2026-03-25
**Reviewed by:** Claude Code (automated review)
**Project:** AI Agent Orchestration Framework
**Tests:** 524 passing

---

## 1. Project Overview

Orchid is a **self-hosted AI agent orchestration framework** for autonomous software development. Installed globally and pointed at external project directories. The framework:

- Manages software projects via a **ReAct-loop agent system** with specialized roles (developer, researcher, reviewer, tester, delegator)
- Supports **pluggable AI backends**: Claude API, llama.cpp (local), Ollama, OpenAI, AWS Bedrock
- Uses **file-based state management** via markdown task boards (`tasks.md`) and hot memory (`CLAUDE.md`)
- Provides **multiple interfaces**: CLI, Web UI (React + FastAPI), Telegram bot, Slack bot
- Supports **multi-project parallelism** with process isolation and shared rate limiting
- Uses **SearXNG** (self-hosted) as primary search, falling back to Brave then DuckDuckGo

**Stack:** Python 3.12+, FastAPI, ChromaDB, sentence-transformers, React 18 + Vite, SQLite-free (JSON/JSONL state)

---

## 2. Architecture

```
orchestrator.py         Main task dispatch loop (Reason→Act→Observe)
  ├─ agents/
  │  ├─ base.py          ReAct loop + tool dispatcher + environment detection
  │  ├─ developer.py     Code generation (enforces file writes)
  │  ├─ tester.py        QA verification — runs tests, structured output, no code writes
  │  ├─ researcher.py    Web search + summarization
  │  ├─ reviewer.py      Quality gates (always routes to Claude)
  │  └─ delegator.py     Sub-agent spawning with depth limits
  ├─ providers/
  │  ├─ registry.py      5-layer routing resolution with caching
  │  ├─ anthropic.py     Claude API (tenacity retry on 429/connection errors, prompt caching)
  │  ├─ local.py         llama.cpp OpenAI-compat
  │  ├─ ollama.py        Ollama
  │  ├─ openai.py        OpenAI / OpenRouter
  │  └─ bedrock.py       AWS Bedrock (boto3 lazy import)
  ├─ memory/
  │  ├─ state.py         tasks.md + CLAUDE.md reader/writer + TaskResultStore
  │  ├─ decisions.py     Append-only decision log (JSON Lines)
  │  └─ vector.py        ChromaDB semantic memory (graceful degradation)
  ├─ tools/
  │  ├─ filesystem.py    read_file, write_file, append_file, list_dir
  │  ├─ shell.py         Bash execution (regex blocklist + optional allowlist + timeout + env detection)
  │  ├─ search.py        SearXNG → Brave → DuckDuckGo with per-query fallback
  │  └─ consistency.py   check_imports() import scanner
  ├─ interfaces/
  │  ├─ cli.py           Typer CLI (--trace, --project defaults to cwd)
  │  ├─ web_server.py    FastAPI + WebSocket streaming + /health + /metrics endpoints
  │  ├─ web_ui/          React + Vite frontend (PM Dashboard, Planning tab, Task board)
  │  ├─ telegram_bot.py  Telegram interface
  │  ├─ slack_bot.py     Slack Socket Mode
  │  ├─ background_runner.py  non-blocking executor shared by Telegram and Slack
  │  └─ central_bot.py   V2.1 central bot server (Telegram + Slack routing)
  ├─ multi.py            Multi-project parallelism (semaphored API + LLM calls)
  ├─ discovery.py        Watchdog-based project auto-discovery
  ├─ session.py          Lifecycle: load → compress → save → embed
  └─ config.py           3-layer merge (defaults → project → CLI)
```

---

## 3. Status of Previous Issues (2026-03-19 → 2026-03-25)

All previously identified issues remain resolved. Additional features and fixes have been merged:

| Feature / Fix | Status |
|---------------|--------|
| `--offline` mode respects all model calls | ✅ Fixed |
| `max_iterations` project override not respected | ✅ Fixed |
| `get_provider_registry` import error in `registry.py` | ✅ Fixed |
| TesterAgent (`type:verify`) | ✅ Added |
| Environment auto-detection (docker/venv/node/python) | ✅ Added |
| `verify_syntax_only` mode | ✅ Added |
| Auto-verify injection after `code_generate` | ✅ Added (opt-in) |
| Task metrics capture (`.orchid/task_metrics.jsonl`) | ✅ Added |
| `--project` defaults to cwd | ✅ Added |
| `--trace` flag for ReAct debugging | ✅ Added |
| PM Dashboard Web UI tab | ✅ Added |
| GET `/api/projects/{id}/metrics` endpoint | ✅ Added |

---

## 4. New Features Detail

### 4.1 TesterAgent
**File:** `orchid/agents/tester.py`

Dedicated QA verification agent. Routes `type:verify` tasks. Does NOT write code — only runs tests and reports structured output:
```json
{"passed": true/false, "tests_run": N, "failures": [...], "files_checked": [...]}
```
Detects the appropriate test runner for the project environment (pytest, jest, docker compose exec).

### 4.2 Environment Auto-Detection
**File:** `orchid/tools/shell.py` (`detect_python_runner`)

At task start, detects project environment: `docker` (docker-compose.yml present), `venv` (.venv/ or venv/), `node` (package.json), `python`. Injected into agent system prompt to inform correct command syntax. Override via `agents.environment` in `.orchid.yaml`.

### 4.3 Task Metrics
**File:** `orchid/orchestrator.py`, `orchid/interfaces/web_server.py`

On every task completion, writes to `.orchid/task_metrics.jsonl`:
- `iters_used` — ReAct iterations consumed
- `duration_s` — wall-clock seconds
- Action counts (read_file, write_file, bash, etc.)
- Blocker details if failed

Exposed via `GET /api/projects/{id}/metrics`.

### 4.4 PM Dashboard Web UI
**Files:** `orchid/interfaces/web_ui/src/` (PMTab components)

New **PM Dashboard tab** in the web UI with five visualizations:
- **MilestoneProgress** — task groups by milestone with completion %
- **DependencyGraph** — cytoscape.js DAG with color-coded task status and critical path highlighting
- **SessionBurndown** — recharts bar chart of tasks completed per session
- **PhaseTimeline** — V2 lifecycle phase duration visualization
- **TaskTiming** — sortable table from `task_metrics.jsonl` with iteration efficiency color coding

### 4.5 CLI Improvements
- `--project` now defaults to the current working directory (helpful error if not an orchid project)
- `--trace` flag logs each ReAct iteration's raw thought/action/observation for debugging stuck agents

---

## 5. Security

### 5.1 Shell Tool ✅
**File:** `orchid/tools/shell.py`

Dual-mode: `blocklist` (default) and `allowlist` (opt-in via `agents.shell_mode: allowlist`). Environment detection does not alter security posture — blocklist patterns always run first regardless of mode.

### 5.2 Path Traversal Protection ✅
`orchid/agents/base.py` — correctly validates all file paths against `project_dir`.

### 5.3 Web UI Has No Authentication ⚠️
`orchid/interfaces/web_server.py` — no auth on any endpoint. Three implementation options documented in a TODO comment at `create_app()`. Acceptable for localhost use; implement before exposing via Traefik without an external auth layer.

### 5.4 Task Metrics and Session Logs ✅
`.orchid/task_metrics.jsonl` and `.orchid/session_logs/` are both gitignored by `orchid init`.

---

## 6. What's Done Well

**Architecture**
- Clean provider abstraction: availability caching, fallback chain, 5-layer routing resolution
- ReAct loop with 4+ action formats (JSON, bracket, heredoc, path) for model compatibility
- Sub-agent context trimming prevents trace bloat in delegation
- File-based state — zero databases, trivially inspectable and diffable
- TesterAgent correctly separated from developer agent (no accidental code writes during verification)

**Resilience**
- All providers fail non-fatally with structured `ProviderError` / `ProviderUnavailableError`
- Search: per-query fallback chain with automatic cache invalidation on failure
- Hot memory auto-compressed when it exceeds threshold
- Process isolation for multi-project runs

**Observability**
- Task metrics capture gives quantitative insight into agent efficiency
- PM Dashboard surfaces metrics visually (burndown, phase timeline, dependency graph)
- `--trace` flag for ReAct debugging
- Live `.live.log` (tailable) + structured `.jsonl` (parseable)
- Real-time WebSocket streaming to web UI
- `/health` endpoint for systemd/Traefik probes

**Testing**
- 524 tests, all passing, no external API calls required
- `@pytest.mark.network` for real-network tests
- End-to-end integration tests covering full Session.load → Orchestrator.run_loop → TaskResultStore path
- GitHub Actions CI on every push/PR

**Documentation**
- Comprehensive README with install, quick-start, CLI reference, architecture, PM Dashboard
- Inline architecture decisions (D0001–D0053) in CLAUDE.md
- `docs/getting-started.md` with worked examples, model routing guidance, shell safety mode, central bot

---

## 7. Remaining Work

### One open item
- **Web UI basic auth** — options documented in `web_server.py` TODO comment; implement Option A (HTTP Basic Auth middleware) when ready to expose the UI beyond localhost

### Acceptable known gaps
- DDG sponsored results: no reliable CSS class to filter; SearXNG is the primary backend
- Web UI has no auth: acceptable for single-user localhost use
- Session logs may contain sensitive content: `.orchid/` is gitignored

---

## 8. Summary Scorecard

| Aspect | Rating | Notes |
|--------|--------|-------|
| Architecture | ⭐⭐⭐⭐⭐ | Clean, layered, pluggable — TesterAgent further specializes the agent tier |
| Code Quality | ⭐⭐⭐⭐⭐ | Provider guards, enum consistency, BPE chunking, ruff clean |
| Security | ⭐⭐⭐⭐ | Shell allowlist mode; systemd hardened; web UI auth pending |
| Error Handling | ⭐⭐⭐⭐⭐ | All providers raise structured errors; retry covers transient failures |
| Performance | ⭐⭐⭐⭐⭐ | Task metrics reveal real bottlenecks; prompt caching reduces API cost |
| Testing | ⭐⭐⭐⭐⭐ | 524 tests; CI on every push; all identified gaps covered |
| Documentation | ⭐⭐⭐⭐⭐ | Excellent README, inline decisions (D0001–D0053), getting-started guide |
| Feature Completeness | ⭐⭐⭐⭐⭐ | TesterAgent, PM Dashboard, task metrics, --trace, env detection all shipped |

---

## 9. Conclusion

Orchid is **production-ready** with one known caveat: the web UI has no authentication, acceptable for localhost use but should be addressed before external exposure. All other issues from previous reviews are resolved.

The codebase continues to improve: TesterAgent provides dedicated QA verification, environment auto-detection eliminates wrong-runner errors in Docker/venv projects, task metrics enable data-driven analysis of agent performance, and the PM Dashboard gives visual project health at a glance. 524 passing tests confirm the additions are solid.
