# Orchid v2.1 vs Claude Code — Feature Gap Analysis

**Date:** 2026-04-14  
**Orchid version:** 2.1 (524 tests, 15.7K SLOC)  
**Claude Code version:** Current (claude-sonnet-4-6)

---

## Executive Summary

Orchid and Claude Code solve adjacent but distinct problems. Orchid is a **project lifecycle orchestration framework** — it takes a project from idea to shipped code across seven phases with structured planning artifacts, task graphs, and multi-model routing. Claude Code is a **developer assistant CLI** — it's optimized for interactive, session-based development work by individuals with rich IDE/platform integration.

Neither is a strict superset of the other. Orchid wins on pipeline automation and lifecycle structure. Claude Code wins on UX, extensibility, and platform reach.

---

## Gap Matrix

| Dimension | Orchid | Claude Code | Delta |
|-----------|--------|-------------|-------|
| Lifecycle phases | 7-phase state machine + gates | None | Orchid ahead |
| Strategic planning agents | Discussion, PM, Project Mgr | Plan mode only | Orchid ahead |
| Task dependency graph | Full DAG, rollup tasks | Visual task list, no DAG | Orchid ahead |
| Multi-model routing | 5-layer resolution, per-agent | Per-session model flag | Orchid ahead |
| Provider support | Anthropic, local, Ollama, OpenAI, Bedrock | Anthropic + Bedrock/Vertex/Foundry | Roughly equal |
| Prompt caching | Explicit (Anthropic) + implicit (llama.cpp) | Not exposed to user | Orchid ahead |
| Vector memory | ChromaDB embedded, BPE chunking, semantic recall | CLAUDE.md + auto memory (file-based) | Orchid ahead |
| Decision log | Append-only JSONL, markdown export | None | Orchid ahead |
| Task metrics | Duration, iterations, DAG burndown | None | Orchid ahead |
| Web UI | FastAPI + React, full dashboard | None (IDE/desktop integrations only) | Orchid ahead |
| Bot integrations | Native Telegram + Slack, CentralBotManager | Slack via MCP, Telegram deprecated | Orchid ahead |
| Project artifacts | REQUIREMENTS.md, ARCHITECTURE.md, MILESTONES.md auto-generated | None | Orchid ahead |
| Multi-project management | Auto-discovery, project switcher, watch dir | Worktrees only | Orchid ahead |
| Background scheduling | APScheduler cron in-process | Routines on Anthropic infra + `/loop` | Claude Code ahead |
| Hook system | None (ReAct tool loop only) | 20+ lifecycle events, 4 hook types | Claude Code ahead |
| MCP support | None | Hundreds of servers, marketplace | Claude Code ahead |
| IDE integration | None | VS Code, JetBrains, Desktop app | Claude Code ahead |
| Permission model | Shell blocklist/allowlist only | 5 modes + granular rules + org-managed | Claude Code ahead |
| Subagent isolation | Delegation (depth 3, no worktrees) | Worktree isolation, full context isolation | Claude Code ahead |
| Skill/plugin system | None | SKILL.md, plugin marketplace, auto-invocation | Claude Code ahead |
| Model effort levels | None | low/medium/high/max (Opus/Sonnet 4.6) | Claude Code ahead |
| Git integration | Via bash tool only | Native (commits, branches, PRs, `--from-pr`) | Claude Code ahead |
| Rewind / session undo | None | `/rewind`, session branching | Claude Code ahead |
| Mobile access | Telegram bot | Dispatch (iOS/Android), web interface | Roughly equal |
| Structured outputs | None | `--json-schema` for typed responses | Claude Code ahead |
| Browser automation | None | Chrome extension, `--chrome` flag | Claude Code ahead |
| Organization controls | None | Managed settings, IT policy enforcement | Claude Code ahead |
| Test coverage | 524 tests, explicit CI | Not applicable (tool, not library) | N/A |

---

## What Orchid Has That Claude Code Lacks

### 1. Project Lifecycle State Machine
Orchid's 7-phase pipeline (NEW → DISCUSSING → REQUIREMENTS → PLANNING → READY → EXECUTING → COMPLETE) with human/auto gates has no equivalent in Claude Code. Claude Code has no concept of project phases, phase transitions, gate approvals, or lifecycle artifacts. Every Claude Code session starts fresh.

**Impact:** Orchid can enforce workflow discipline across sessions and teams. Claude Code relies entirely on the user managing project state externally.

### 2. Strategic Planning Agents
Three dedicated strategic agents (DiscussionAgent, ProductManagerAgent, ProjectManagerAgent) produce structured planning artifacts:
- `REQUIREMENTS.md` — feature specification
- `ARCHITECTURE.md` — system design
- `MILESTONES.md` — delivery plan
- `tasks.md` — executable task board

Claude Code has `/plan` mode (read-only analysis before execution) but no persistent planning artifacts and no agents that interview the user to generate structured project documentation.

### 3. Task Dependency Graph with 11 Task Types
Orchid's `tasks.md` format supports `needs:T001,T002` dependencies, rollup aggregation, 11 typed task categories, and priority ordering. The orchestrator resolves DAGs, detects cycles, and respects blocking relationships.

Claude Code's task system is a visual in-session checklist. No dependencies, no types, no DAG, no cross-session persistence of task state.

### 4. Multi-Model Routing (5-Layer Resolution)
Orchid routes different task types to different models automatically:
- Review/planning → Claude
- Code generation/testing → local (llama.cpp/Ollama)
- Overridable at CLI, project config, task annotation, or agent levels

Claude Code uses one model per session. The user can change it mid-session but there's no automatic per-task routing. No built-in Ollama or llama.cpp integration — Claude Code is Anthropic-only by design.

### 5. Embedded Vector Memory
ChromaDB embedded with BPE-aware chunking, auto-embedding of session logs and decisions, and semantic recall at session start. Meaningful for large projects with months of history.

Claude Code's auto memory is file-based keyword storage, not semantic search. No vector index, no similarity search.

### 6. Decision Log
Append-only `decisions.json` (JSONL) tracks architectural decisions with rationale and context. Exported as markdown for documentation. Seeded via `orchid decide` CLI.

No equivalent in Claude Code. Decisions made during a session evaporate unless manually saved by the user.

### 7. Web Dashboard (FastAPI + React)
Full multi-project dashboard with:
- PM dashboard: milestone progress, dependency DAG, session burndown, phase timeline
- Task board with inline editing
- Discussion streaming panel
- Hot memory viewer/editor
- Vector recall search
- Session log replay
- Project config editor

Claude Code has no web UI. Access is via CLI, IDE plugins, Desktop app, or web sessions at claude.ai/code — none of which provide a project management dashboard.

### 8. Native Telegram + Slack Bots with Project State
CentralBotManager runs both bots from a single process. Telegram persists per-user project context across messages. Slack maps channels to projects automatically.

Claude Code has a Slack integration (via MCP), but it's Anthropic-controlled and doesn't have persistent project routing, per-channel project assignment, or multi-project switching commands.

### 9. Prompt Caching as First-Class Feature
Orchid explicitly manages Anthropic's `cache_control` blocks for system prompts and tracks cache hit rates per session. For llama.cpp/Ollama it reorders stable prefix content and sets `cache_prompt: true` on every request.

Claude Code doesn't expose caching controls to users. Token savings from caching are invisible.

### 10. Environment Auto-Detection
Orchid injects environment context (docker / venv / node / python) into agent prompts and rewrites commands accordingly (e.g., `python` → `.venv/bin/python`).

Claude Code relies on the user or CLAUDE.md to document environment setup.

### 11. Task Metrics + PM Dashboard Charts
Per-task duration, ReAct iteration counts, and tool action frequencies stored in `task_metrics.jsonl`. Visualized as sortable tables, burndown charts, and phase timelines in the web UI.

No equivalent in Claude Code.

---

## What Claude Code Has That Orchid Lacks

### 1. Hook System (20+ Events, 4 Hook Types)
Claude Code's hooks fire at `PreToolUse`, `PostToolUse`, `PermissionRequest`, `Stop`, `SessionStart`, and 15+ other lifecycle points. Hooks can block execution, transform inputs, inject context, or call HTTP endpoints.

Orchid has no hook system. Cross-cutting concerns (logging, validation, notifications) must be baked into agent code or handled externally.

**Impact:** Claude Code users can implement linting on every file write, auto-commit after edits, security scanning before Bash execution, etc. — without modifying Orchid's core.

### 2. MCP (Model Context Protocol) Support
Hundreds of pre-built MCP servers (GitHub, Jira, Notion, Slack, Google Drive, AWS, databases). Claude Code tools appear automatically as `mcp__<server>__<tool>`. Permission rules can filter per-server.

Orchid has no MCP layer. Integrating external services requires writing a new provider or tool class and wiring it into the agent.

### 3. IDE & Desktop App Integration
Claude Code works natively inside VS Code and all JetBrains IDEs with inline diffs, file context, and conversation history. Desktop app supports side-by-side diff review, multiple sessions, and mobile Dispatch.

Orchid is CLI-only. No IDE plugins, no desktop UI, no mobile app.

### 4. Granular Permission System
5 permission modes + allow/ask/deny rules with glob patterns, tool-specific matching, domain filtering, and organization-managed policy enforcement. Can lock down to specific commands (`Bash(npm test)`) or file paths (`Edit(src/**)`).

Orchid has shell blocklist/allowlist (binary: all commands or an explicit list). No per-file-path restrictions, no per-tool rules, no org-level policy.

### 5. Subagent Worktree Isolation
Claude Code subagents can run in isolated git worktrees — changes don't touch the main branch until merged. Agents have full tool access within their isolated copy.

Orchid's delegation limits to 3 levels and shares the project directory. No git worktree isolation; sub-agents write to the same filesystem as the parent.

### 6. Git-Native Features
`--from-pr` flag to resume sessions linked to GitHub PRs. PR status displayed in terminal footer. `gh pr create` integration. Automatic branch creation. Session auto-linking to open PRs.

Orchid treats git as just another bash command. No PR-linked sessions, no branch tracking, no PR status display.

### 7. Skill & Plugin Marketplace
Claude Code has a skill system (SKILL.md files with prompt bodies) and a plugin marketplace with installable MCP servers. Skills auto-invoke based on user message patterns. Plugins compose skills + MCP servers + hooks into installable packages.

Orchid has no skill or plugin abstraction. All behavior is code.

### 8. Effort Levels (Adaptive Reasoning)
`low/medium/high/max` effort on Opus 4.6 and Sonnet 4.6 controls how much chain-of-thought reasoning the model does. High-effort tasks get more compute; routine tasks stay cheap.

Orchid uses model routing (Claude vs local) as a proxy for effort but has no fine-grained reasoning depth control within a single model.

### 9. Session Rewind and Branching
`/rewind` lets users undo changes back to a specific point in the session. `/branch` forks a session to explore a different approach without losing the original path.

Orchid has no session undo. Task results are final once written; no branching of orchestration state.

### 10. Structured Output (JSON Schema)
`--json-schema` forces model responses to match a typed schema. Useful for programmatic consumers of Claude Code output.

Orchid agents return unstructured text. Structured task output is a convention (markdown checklists, JSON files) but not enforced by schema.

### 11. Browser Automation
Chrome extension + `--chrome` flag for live DOM inspection, element selection, network monitoring, and automated web testing.

Orchid has web search (SearXNG/Brave/DDG) but no browser automation or page interaction.

### 12. Organization-Level Policy Enforcement
IT-deployed managed settings (`/Library/Application Support/ClaudeCode/`) can lock down models, disable dangerous modes, enforce permission rules across all users in an organization.

Orchid has no multi-user or organization management layer.

### 13. `--output-format stream-json` for Programmatic Use
Headless mode (`-p`) with `stream-json` output format streams structured events for programmatic consumers, CI pipelines, and external orchestration.

Orchid has WebSocket streaming for its own web UI but no equivalent machine-readable output mode for external consumption.

---

## Feature Gaps: Strategic Priority Assessment

### High Priority — Orchid Should Adopt from Claude Code

| Feature | Effort | Value | Rationale |
|---------|--------|-------|-----------|
| Hook system (pre/post tool) | High | High | Would decouple cross-cutting logic from agent code; enable user customization without forking |
| MCP adapter layer | High | High | Access to hundreds of integrations for free; eliminates need to write custom providers |
| Granular permission rules | Medium | Medium | Current blocklist/allowlist is too coarse for team environments |
| `--output-format stream-json` | Low | High | External tools could consume Orchid output; easy to add to FastAPI `/run` endpoint |
| Session rewind | Medium | Medium | Task results are irreversible today; at minimum, checkpoint before each task |

### High Priority — Claude Code Could Adopt from Orchid

| Feature | Effort | Value | Rationale |
|---------|--------|-------|-----------|
| Task dependency DAG | High | High | Claude Code's task list has no ordering guarantees or blocking logic |
| Multi-model routing per task type | Medium | High | Significant cost savings routing simple tasks to cheaper/local models |
| Lifecycle phases with gates | High | Medium | Long-running projects have no phase structure; everything is one flat session |
| Vector memory + semantic recall | High | Medium | File-based auto memory doesn't scale to large, long-running projects |
| Per-task metrics | Low | Medium | No visibility into which tasks consume most tokens/time |
| Prompt caching controls | Low | High | Users have no control or visibility into caching; easy win for cost reduction |

---

## Architectural Differences That Explain the Gaps

| Aspect | Orchid | Claude Code |
|--------|--------|-------------|
| **Execution model** | Persistent background process; projects managed across sessions | Ephemeral sessions; each `claude` invocation is independent |
| **State storage** | File-based per project (`.orchid/`, `tasks.md`, `CLAUDE.md`) | File-based per user (`~/.claude/projects/`, `CLAUDE.md`) |
| **Agent model** | Text-based ReAct (Thought→Action→Observation parsed from LLM output) | Tool-use API (structured function calls in JSON) |
| **Target user** | Technical user automating multi-agent pipelines | Individual developer in active coding sessions |
| **Extensibility** | Config + code changes | Hooks + skills + plugins + MCP |
| **Concurrency** | Single process, thread-based | Single process, with subagent isolation via worktrees |
| **Platform footprint** | CLI + Web UI + bots | CLI + IDE + Desktop + Web + Mobile |

Orchid's text-based ReAct loop predates Anthropic's tool-use API and gives it flexibility to run against any OpenAI-compatible endpoint, but it means tool calls are fragile (LLM must format text correctly) and there's no equivalent to Claude Code's typed tool-use schema.

Claude Code's hooks and MCP systems assume the model is Anthropic Claude. Orchid's multi-provider design is incompatible with hooks that call `anthropic.beta` features.

---

## Unique to Orchid (No Claude Code Equivalent)

1. **7-phase lifecycle state machine** with human/auto gate approvals
2. **Strategic planning agents** (Discussion, ProductManager, ProjectManager)
3. **Auto-generated project artifacts** (REQUIREMENTS, ARCHITECTURE, MILESTONES)
4. **Task dependency graph** (DAG with rollup aggregation)
5. **Explicit multi-model routing** per task type and agent
6. **Prompt caching controls** exposed to users (Anthropic + llama.cpp)
7. **Embedded vector memory** (ChromaDB + BPE chunking + semantic recall)
8. **Decision log** (append-only JSONL with rationale)
9. **Project-scoped web dashboard** (FastAPI + React)
10. **Native Telegram + Slack bots** with per-user project state
11. **Task metrics** (duration, iterations, tool counts, burndown)
12. **Multi-project auto-discovery** (watchdog, project switcher)
13. **Machine profile** (developer prefs injected into planning prompts)
14. **Environment auto-detection** (docker/venv/node/python → command rewriting)
15. **Agent delegation** (subtask spawning with depth limit)

## Unique to Claude Code (No Orchid Equivalent)

1. **Hook system** (20+ events, 4 hook types, blocking/non-blocking)
2. **MCP server support** (marketplace, hundreds of integrations)
3. **IDE integration** (VS Code, JetBrains)
4. **Desktop + mobile app** (diff viewer, Dispatch messaging)
5. **Subagent worktree isolation** (isolated git branches per agent)
6. **Skill marketplace** (SKILL.md + plugin packaging)
7. **Session rewind/branch** (undo to checkpoint, fork session)
8. **PR-linked sessions** (`--from-pr`, PR status display)
9. **Model effort levels** (low/medium/high/max reasoning depth)
10. **Granular permission rules** (per-tool, per-path, per-domain, org-managed)
11. **Structured output schema** (`--json-schema` for typed responses)
12. **Browser automation** (Chrome extension + `--chrome`)
13. **Organization policy enforcement** (managed settings, IT deploy)
14. **Routines** (Anthropic-hosted scheduled tasks)

---

## Bottom Line

Orchid is a **pipeline orchestrator disguised as a CLI**. It shines for autonomous, long-running, multi-step projects where task ordering, model cost optimization, and team notification matter more than interactive responsiveness.

Claude Code is a **developer assistant disguised as an orchestration framework**. It shines for interactive work, short-cycle feedback loops, IDE integration, and extensibility via hooks/MCP.

The most impactful single addition Orchid could make would be a **hook system** — it would unlock user customization, policy enforcement, and third-party integrations without requiring changes to agent code. The most impactful addition Claude Code could make for power users would be **task dependency DAG support** and **automatic multi-model routing** to reduce costs on large autonomous runs.
