<!-- compressed 2026-03-26 -->

# CLAUDE.md — Orchid Framework (v2.1)

## Core
Standalone AI agent orchestration. Tool (`~/orchid/`) invokes external projects (`~/projects/<name>/`). Projects opt-in via `CLAUDE.md` + `tasks.md` + `.orchid.yaml`.

## Layout
`~/projects/<name>/.orchid/`: `decisions.json`, `session_logs/`, `chroma/`, `task_results.json`.

## CLI
`orchid --project <path> --mode auto|interactive [--code-model] [--provider] [--offline]`
`orchid init <path>`, `orchid decide "Title" --decision "..."`, `orchid new "<desc>"`.
`orchid serve [--watch-dir] [--port 7842] [--telegram|--slack|--bots]` (Unified entry).
`orchid --status|--recall "q"|--search "q"|--add-task "t"|--run-task T001|--approve`.
`orchid --check-providers`.
*Deprecated:* `orchid telegram|slack|web` → use `orchid serve --telegram/--slack`.

## Tasks (`tasks.md`)
`- [ ] **T001** Title \`type:code_generate\` \`p1\` \`needs:T002\` \`model:claude\``.
Skip: `- [~] **T003**`. Rollup: `- [ ] **T099** \`type:rollup\` \`rollup:T090,T091\` \`output:FILE.md\``.

## Tool Calls (ReAct)
`Action: <name>\nAction Input: <json>`. Actions: `read_file`, `list_dir`, `bash`, `write_file` (replace), `append_file` (add), `delegate`.

## Architecture Decisions
**D0001** File-state. **D0002** 2-tier routing (Claude/llama). **D0003** ReAct text. **D0004** Interface-agnostic. **D0005** 3-layer config. **D0006** Standalone runtime. **D0007** Embed Chroma. **D0008** Embed: llama→ST. **D0009** Auto-embed/recall. **D0010** Search: SearXNG→Brave. **D0011** Extract: trafilatura. **D0012** Delegate depth 3. **D0013** Sub-context. **D0014** Telegram logic. **D0015** User whitelist. **D0016** Model routing. **D0017** Task deps. **D0018** Live log. **D0019** Inject queue. **D0020** Telegram notify. **D0021** Process parallelism. **D0022** Claude sem. **D0024** Slack Socket. **D0025** Slack threads. **D0026** Shared Runner. **D0027** Web FastAPI/React. **D0028** React dist. **D0029** Traefik TLS. **D0030** ProviderBase ABC. **D0031** Shared backends. **D0032** Provider check. **D0033** Watchdog. **D0034** Orchid serve. **D0035** AgentManager. **D0036** XDG config. **D0037** Rollup Claude. **D0038** TaskResultStore. **D0039** Shell allowlist. **D0040** Tiktoken chunking. **D0041** V2 Lifecycle. **D0042** Strategic agents. **D0043** Gates. **D0044** Machine profile. **D0045** Web Planning. **D0046** WS Stream. **D0047** Wizard. **D0048** Prompt cache. **D0049** KV cache. **D0050** CentralBot. **D0051** Telegram state. **D0052** Slack map. **D0053** Bot serve.

## Current State
**V2.1 Complete. 446+ tests passing.**
*   **T051** Shell allowlist + BPE chunking.
*   **T053** V2 lifecycle + strategic agents.
*   **T054/55** Web UI Planning tab + Discussion streaming.
*   **T056** Prompt caching (D0048).
*   **T058–T059** Code review anthropic.py.
*   **T060** File Writing Guidelines.
*   **T061** CentralBotManager.
*   **T064** Fix --log-level.
*   **T066** README V2.1 docs.
*   **T068** systemd service.
*   **T077/78** Docs/README updated.
*   **T086** PM Guide (`docs/pm-guide.md`): Workflow, Wizard, Phases, Dashboard, Mobile monitoring, Glossary.

## Install
`uv venv && uv pip install -e ".[dev]"`. Env: `~/.config/orchid/.env`. `ANTHROPIC_API_KEY` required.