# Orchid V2 / V2.1 Feature Summary

**Last updated:** 2026-03-25
**Tests:** 524 passing

---

## Overview

Orchid V2 introduced a lifecycle-driven planning engine and strategic agent architecture. V2.1 added the central bot server, PM Dashboard, TesterAgent, task metrics, and environment detection — completing the idea-to-working-software pipeline.

---

## Lifecycle State Machine

Projects follow a structured 7-phase lifecycle:

```
NEW → DISCUSSING → REQUIREMENTS → PLANNING → READY → EXECUTING → COMPLETE
```

| Phase | What happens |
|-------|-------------|
| NEW | Project scaffolded, no discussion yet |
| DISCUSSING | Interactive chat with DiscussionAgent (Claude) |
| REQUIREMENTS | ProductManagerAgent generates REQUIREMENTS.md + ARCHITECTURE.md |
| PLANNING | ProjectManagerAgent generates MILESTONES.md + tasks.md |
| READY | Artifacts reviewed; human gate passed |
| EXECUTING | Agents run tasks in priority order |
| COMPLETE | All tasks done |

Gates between phases are `human` by default (require `--approve`). Override per-gate in `.orchid.yaml`:
```yaml
gates:
  ready_to_executing: auto
```

---

## Agent Tiers

### Claude-tier Agents (high-level, orchestration)
| Agent | Task types |
|-------|-----------|
| Orchestrator | `orchestrate` — picks next task, plans approach |
| DiscussionAgent | `discuss` — elicits requirements via conversation |
| ProductManagerAgent | `plan` — generates REQUIREMENTS.md, ARCHITECTURE.md |
| ProjectManagerAgent | `plan` — generates MILESTONES.md, tasks.md |
| Reviewer | `review` `critique` — quality gates |
| Synthesizer | `synthesize` `rollup` — aggregates results |

### Local-tier Agents (high-volume, cost-efficient)
| Agent | Task types |
|-------|-----------|
| Developer | `code_generate` — code generation, editing, debugging |
| TesterAgent | `verify` — runs tests, structured pass/fail output, no code writes |
| Researcher | `search` `summarize` — web search and summarization |
| Draft | `draft` — rapid prototyping and documentation |

### Model routing
```
CLI flag → task model: tag → keyword heuristic → type default → hardcoded fallback
```

---

## TesterAgent (V2.1)

`orchid/agents/tester.py` — dedicated QA verification agent for `type:verify` tasks.

- Does NOT write or modify code
- Detects the correct test runner for the project environment
- Returns structured JSON output: `{passed, tests_run, failures, files_checked}`
- Optionally auto-injected as a paired verify task after `code_generate` completes (opt-in: `auto_verify: true` in `.orchid.yaml`)

---

## Environment Auto-Detection (V2.1)

At task start, Orchid detects the project environment and injects it into the agent's system prompt:

| Environment | Detection | Runner |
|-------------|-----------|--------|
| `docker` | `docker-compose.yml` present | `docker compose exec <svc> python -m pytest` |
| `venv` | `.venv/` or `venv/` present | `.venv/bin/python -m pytest` |
| `node` | `package.json` present | `npm test` / `npx jest` |
| `python` | fallback | `python3 -m pytest` |

Override per-project: `agents.environment: docker` in `.orchid.yaml`.

`verify_syntax_only: true` mode skips pytest/jest entirely and only runs `py_compile` / `node --check` — useful in CI environments without full test dependencies.

---

## Task Metrics (V2.1)

On every task completion, Orchid writes to `.orchid/task_metrics.jsonl`:

```json
{"task_id": "T001", "duration_s": 42.3, "iters_used": 7, "actions": {"bash": 3, "write_file": 2}, "blocker": null}
```

- `GET /api/projects/{id}/metrics` exposes aggregated metrics via REST
- PM Dashboard reads this file for all visualizations

---

## PM Dashboard (V2.1)

New **PM Dashboard tab** in the Web UI with five components:

| Component | Description |
|-----------|-------------|
| MilestoneProgress | Task groups by milestone with completion % bars |
| DependencyGraph | cytoscape.js DAG — color-coded task status, critical path highlighting |
| SessionBurndown | recharts bar chart — tasks completed per session |
| PhaseTimeline | V2 lifecycle phase duration visualization |
| TaskTiming | Sortable table from `task_metrics.jsonl` with iteration efficiency color coding |

---

## Web UI Planning Tab (V2)

Real-time visibility into the V2 lifecycle:

| Component | Description |
|-----------|-------------|
| PhaseIndicator | Shows current phase and gate status |
| DiscussionPanel | Chat with AI PM, streaming via WebSocket, persistent history |
| ArtifactPanel | Browse REQUIREMENTS.md, ARCHITECTURE.md, MILESTONES.md |
| ApprovalPanel | One-click gate approval |
| NewProjectWizard | 4-step modal to create new projects from the browser |

---

## Prompt Caching

**Anthropic (explicit):** System prompts ≥ 2048 chars wrapped with `cache_control: {type: ephemeral}`. DiscussionAgent separates static instructions (always cached) from dynamic conversation history. Session cache write/read counts logged at close.

**llama.cpp / Ollama (implicit):** `cache_prompt: true` on every request. `optimize_for_caching()` places stable content before dynamic content to maximise prefix reuse. Cache hits detected from response timings (< 1.0 ms/token).

Benefits: up to 70% cost reduction on Claude API calls, 3–5× faster repeated prompts.

---

## Central Bot Architecture (V2.1)

Single `CentralBotManager` in `orchid/interfaces/central_bot.py` unifies Telegram and Slack under `orchid serve`:

```bash
orchid serve --bots --watch-dir ~/LocalAI
orchid serve --telegram
orchid serve --slack
```

- Telegram state at `~/.config/orchid/telegram-state.json`; per-user active project; `/orchid_switch` to change
- Slack channel map at `~/.config/orchid/slack-channels.json`; auto-creates `#{name}-project` on discovery; global commands in `#orchid-general`
- Both bots share `BackgroundRunner` for non-blocking agent execution
- Old `orchid telegram` / `orchid slack` subcommands deprecated with warning

---

## CLI Improvements (V2.1)

| Flag | Description |
|------|-------------|
| `--trace` | Log each ReAct iteration's raw thought/action/observation (debugging) |
| `--project` defaults to cwd | `orchid --status` works without `--project` flag if run from project dir |
| `--offline` | Respected by all model call paths (previously some bypassed it) |

---

## Architecture Decisions (D0001–D0053)

Full decision log in `CLAUDE.md`. Key decisions:

| # | Decision |
|---|----------|
| D0001 | File-based state — no DB |
| D0002 | Two-tier routing: Claude ↔ local |
| D0003 | ReAct loop, text-parsed tool calls |
| D0004 | Interface-agnostic core |
| D0005 | Three-layer config (defaults → .orchid.yaml → CLI) |
| D0006 | Standalone runtime (projects not subfolders) |
| D0007 | ChromaDB embedded vector store |
| D0027 | Web UI: FastAPI + React at port 7842 |
| D0030 | ProviderBase ABC with 60s availability cache, 5-layer resolution |
| D0039 | Shell dual-mode: blocklist (default) / allowlist |
| D0041 | V2 lifecycle: 7-phase state machine |
| D0048 | Anthropic explicit prompt caching |
| D0050 | CentralBotManager for Telegram + Slack |
