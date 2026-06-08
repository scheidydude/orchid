# Changelog

## V3.2
Completes CLI auth integration: `orchid login/logout/whoami` authenticate against a running server; `--mode auto` and `--run-task` automatically inject vault credentials, enforce LLM/CPU budgets, and record spend for the logged-in user; `orchid mcp ls/call` respects per-user catalog ACLs; `orchid user`, `orchid scheduler`, and `orchid audit` subcommands provide admin and ops CLI access to the full multi-user API.

## V3.1
Hardening pass: live Telegram/Slack DM notifications from scheduled task runs, `allow_user_projects` flag enforcement across web/Telegram/Slack, per-user project ownership registry with user-namespaced paths, and a production-ready PostgreSQL auth backend with one-command migration from the JSON store.

**Portal scheduler UX:** timezone-aware visual schedule builder, per-row enable/disable toggle, JSON task export/import for sharing, Save & Test workflow (creates task then runs it, auto-closes on success), an MCP tool browser for `mcp_tool` / `agent_tool` types, and a conversational Task Wizard that interviews the user in plain language and fills in the task form automatically. Auth tokens extended to 8 h with silent refresh so users are not prompted to log in during a normal working day.

## V3.0
Transforms Orchid into a full multi-user agentic OS: per-user credential vault (Fernet/HKDF), admin-managed MCP server catalog with role/user-based access control, per-user LLM spend and CPU-time budgets enforced at execution time, two React SPAs (User Portal `/app/` and Admin Console `/admin/`), admin-invite flow, and a System Config page for live runtime settings.

## V2.5
Cron-based scheduled task manager: per-user schedules stored as dicts on User, APScheduler-backed engine, `agent_prompt`/`mcp_tool`/`shell` task types, append-only JSONL run history with 30-day pruning, and a full `/api/scheduler/*` REST API.

## V2.4
OS-grade reliability: graceful shutdown, orphan recovery, subprocess worker pool, task preemption/pause-resume, WebSocket backpressure, CPU/latency budgets, and an ordered provider fallback chain that retries on 429/502/503 before marking a task BLOCKED.

## V2.3
Full multi-user auth: JWT sessions, argon2id passwords, API keys with scope enforcement, Google/Entra/OIDC SSO, PKCE mobile flow, append-only audit log, per-user project scoping, a React login page, and pluggable auth storage (file or PostgreSQL).

## V2.2
Parallel task dispatch, native git tools, worktree isolation, dynamic task spawning, a cross-project agent pool, and cost-aware scheduling.

## V2.1
Central bot server unifying Telegram and Slack management under a single `orchid serve` command, replacing per-project bot commands with a multi-project routing architecture.

## V2.0
Full idea-to-execution lifecycle pipeline: discuss requirements with an AI product manager, generate architecture docs, break work into milestones, then execute tasks with specialized agents. Lifecycle state machine: `NEW → DISCUSSING → REQUIREMENTS → PLANNING → READY → EXECUTING → COMPLETE`.

## V1.0
Initial release: standalone AI agent orchestration framework with ReAct loop, file-state task board, 2-tier model routing (Claude / llama.cpp), and Telegram/Slack bot interfaces.
