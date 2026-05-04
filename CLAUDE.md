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
**D0001** File-state. **D0002** 2-tier routing (Claude/llama). **D0003** ReAct text. **D0004** Interface-agnostic. **D0005** 3-layer config. **D0006** Standalone runtime. **D0007** Embed Chroma. **D0008** Embed: llama→ST. **D0009** Auto-embed/recall. **D0010** Search: SearXNG→Brave. **D0011** Extract: trafilatura. **D0012** Delegate depth 3. **D0013** Sub-context. **D0014** Telegram logic. **D0015** User whitelist. **D0016** Model routing. **D0017** Task deps. **D0018** Live log. **D0019** Inject queue. **D0020** Telegram notify. **D0021** Process parallelism. **D0022** Claude sem. **D0024** Slack Socket. **D0025** Slack threads. **D0026** Shared Runner. **D0027** Web FastAPI/React. **D0028** React dist. **D0029** Traefik TLS. **D0030** ProviderBase ABC; resolution order: CLI > project providers.<agent> > project providers.task_types.<type> > task annotation > env > type/agent defaults. **D0031** Shared backends. **D0032** Provider check. **D0033** Watchdog. **D0034** Orchid serve. **D0035** AgentManager. **D0036** XDG config. **D0037** Rollup Claude. **D0038** TaskResultStore. **D0039** Shell allowlist. **D0040** Tiktoken chunking. **D0041** V2 Lifecycle. **D0042** Strategic agents. **D0043** Gates. **D0044** Machine profile. **D0045** Web Planning. **D0046** WS Stream. **D0047** Wizard. **D0048** Prompt cache. **D0049** KV cache. **D0050** CentralBot. **D0051** Telegram state. **D0052** Slack map. **D0053** Bot serve.

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
## Recent Completions

- [T091] Update docs/pm-guide.md: add section on configuring fully-local operation via .orchid.yaml providers overrides. Show example config for all-local PM planning and development with Claude only for final review. Explain the resolution order: CLI flag > project config > task annotation > defaults.: Task T091 complete. Updated `docs/pm-guide.md` with a comprehensive new section on **Configuring Fully-Local Operation** that includes:

1. **Provider Resolution Order** — Clear table showing the prio

- [T096] Wire hooks into task lifecycle: FAILED: [max iterations reached without final answer]

- [T096] Wire hooks into task lifecycle: Hooks have been successfully wired into the task lifecycle (T096) and session/phase transitions (T097). The implementation includes:

- **Task lifecycle hooks** in orchestrator.py: task_start, task_co

- [T095] Wire hooks into agent ReAct loop: \s*(.*)", re.DOTALL)
_THOUGHT_RE = re.compile(r"Thought:\s*(.*?)(?=\nAction:|\nFinal Answer:|$)", re.DOTALL)

_TOOL_ARG_MAP: dict[str, str] = {
    "read_file": "path", "list_dir": "path", "append_fil

- [T092] Design and implement: The hooks system for Orchid V2 has been fully designed and implemented. All components are in place:

1. **Core hook infrastructure** (`orchid/hooks/`) - Events, types, registry, loader
2. **Integrati

- [T093] Define hook event constants in: Hook event constants have been successfully defined in `orchid/hooks/events.py` and properly exported in `orchid/hooks/__init__.py`. The implementation includes:

**Agent ReAct loop events:**
- `AGENT

- [T094] Implement hook loader: The hook loader implementation (T094) is complete. The HookLoader class loads hook configurations from `.orchid.yaml`, supports shell/http/python hook types with sync/async/background execution modes,

- [T097] Wire hooks into session and phase transitions: fire: T097 "Wire hooks into session and phase transitions: fire" is **complete**. The hook system was already fully implemented in the codebase. Here's what was verified:

## Hook Integration Points Impleme

- [T098] Add hook config schema to: The hook config schema has been successfully added to the Orchid framework. The schema module (`orchid/hooks/schema.py`) provides Pydantic-based validation for all hook configurations including shell,

- [T099] Add CLI:: The hooks CLI has been successfully implemented in `orchid/interfaces/hooks_cli.py` with 7 subcommands (list, show, validate, test, stats, add, remove). All commands have been tested and are working c

- [T100] Review hook registry and loader implementation: verify blocking hooks cannot deadlock the orchestrator, shell hooks are sandboxed by the existing shell allowlist, http hooks respect timeout, and hook errors are logged but never crash the agent loop. Check: I have completed the security review for task **T100**. Here's a summary of the findings:

## Review Summary

### Files Analyzed
- `orchid/hooks/registry.py` - Hook registry and execution
- `orchid/ho

- [T101] Review hook integration points in: Task T101 **Review hook integration points** is now COMPLETE.

## Summary

I have completed the review of hook integration points in the Orchid framework. The following files were created/updated:

##

- [T102] Unit tests: Unit tests for the hook system have been created at `tests/test_hooks.py` with 69 tests covering HookEvent, HookRegistry, HookLoader, hook type classes (ShellHook, HTTPHook, PythonHook), and schema va

- [T103] Unit tests: FAILED: [max iterations reached without final answer]

- [T104] Integration tests: Task completed successfully."


@pytest.fixture()
def project_with_hooks(tmp_path: Path) -> Path:
    """Minimal orchid project with hooks configuration."""
    (tmp_path / "tasks.md").write_text(

- [T103] Unit tests: The answer is 42")

    assert action is None
    assert args is None


# ── Tool execution tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_r
