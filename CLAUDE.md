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

- [T105] Create `orchid/mcp/types.py`. Also create empty `orchid/mcp/__init__.py` with content `# MCP adapter layer`. Define exactly these three dataclasses and nothing else:: Created two files:

1. **`orchid/mcp/__init__.py`** — contains just `# MCP adapter layer`
2. **`orchid/mcp/types.py`** — defines exactly three dataclasses:
   - `MCPTool(name, description, parameters)

- [T106] Create `orchid/mcp/client.py`. Define exactly one ABC and one exception class:: Created `orchid/mcp/client.py` with:
- **`MCPClientError`** — exception class with `message` and `code` attributes
- **`MCPClient`** — ABC with four abstract methods: `connect()`, `disconnect()`, `lis

- [T107] Create `orchid/mcp/stdio_client.py`. Implement exactly this class using `subprocess.Popen`:: File `orchid/mcp/stdio_client.py` created with the `StdioMCPClient` class implementing the `MCPClient` ABC via `subprocess.Popen`, with full JSON-RPC 2.0 transport over stdin/stdout pipes. The `__init

- [T124] Create `tests/test_mcp_integration.py`. Write exactly 1 test function. Start a real Python subprocess as a minimal MCP server using `python3 -c "<inline script>"`. The inline script must: listen on stdin, respond to `initialize` with `{"jsonrpc":"2.0","id":0,"result":{}}`, respond to `tools/list` with one tool named `echo`, respond to `tools/call` with `{"content": arguments["msg"], "isError": false}`. Test: `StdioMCPClient.connect()`, `list_tools()` returns `[MCPTool("echo",…)]`, `call_tool("echo", {"msg":"hello"})` returns `MCPResult(content="hello")`. Use `pytest.mark.skipif(sys.platform=="win32", reason="POSIX only")`.: Thought: I need to understand the existing MCP module structure before writing the integration test. Let me check the existing files.

- [T108] Create `orchid/mcp/http_client.py`. Implement exactly this class using `httpx.Client` (sync):: File `orchid/mcp/http_client.py` has been created with the `HTTPMCPClient` class implementing the `MCPClient` ABC using `httpx.Client` (sync).

- [T109] Create `orchid/mcp/adapter.py`. Implement exactly this class:: T109 `orchid/mcp/adapter.py` created with the `MCPAdapter` class — a synchronous adapter that wraps an `MCPClient` and exposes `connect()`, `disconnect()`, `list_tools()`, and `call_tool()` methods fo

- [T110] Create `orchid/mcp/manager.py`. Implement exactly this class. It is fully synchronous:: The file has been written and verified. Here's a summary of what `orchid/mcp/manager.py` implements:

**`MCPManagerError`** — A subclass of `MCPClientError` raised for configuration or lifecycle error

- [T111] Extend `orchid/orchid.defaults.yaml` — append MCP servers section. Read the file first to find its end. Append exactly this block at the bottom of the file:: T111 complete. The `orchid/orchid.defaults.yaml` file now includes the `mcp_servers:` section at the bottom with documentation explaining transport types (stdio/http) and an example configuration.

- [T112] Extend `orchid/config.py` — add one helper function. Read the file first. Append after the last function in the file:: Done. Added `get_mcp_servers()` helper to `orchid/config.py` — it returns the `mcp_servers` dict from the merged config, defaulting to `{}` if not present.

- [T113] Extend `orchid/orchestrator.py` — wire `MCPManager` into task execution. Read the file first. Make exactly two changes:: T113 complete — `orchid/orchestrator.py` has been updated with two changes: (1) `_ensure_mcp_connected()` lazily creates/connects `MCPManager` on first task execution, and (2) MCP tools are injected i

- [T114] Extend `orchid/runner.py` — create and teardown `MCPManager` around the run loop. Read the file first. In `BackgroundRunner._run()` (line 70), make exactly these two changes:: T114 is complete. Two changes were made to `orchid/runner.py` in `BackgroundRunner._run()`:

1. **Before the task loop** — an `MCPManager` is created, servers are discovered via `discover_servers()`,

- [T115] Extend `orchid/interfaces/cli.py` — add `mcp` Typer sub-app with two commands. Read the file first. Find the pattern where sub-apps are registered (search for `app.add_typer`). Add a new sub-app with these two commands:: Task T115 complete. The `mcp` sub-app has been added to `orchid/interfaces/cli.py` with two commands (`ls` and `call`), registered via `app.add_typer(mcp_app)`, and verified to work correctly via `orc

- [T116] Review `orchid/mcp/stdio_client.py` for exactly these 4 issues. For each, report PASS or FAIL with the line number:: Thought: The task is to review `orchid/mcp/stdio_client.py` for exactly 4 issues and report PASS or FAIL with line numbers. This is a review task — no file needs to be written. I already read the file

- [T117] Review `orchid/mcp/http_client.py` for exactly these 4 issues. For each, report PASS or FAIL with the line number:: Review complete. All 4 issues reported as FAIL with line numbers: Issue 1 (line 93), Issue 2 (line 53), Issue 3 (line 96), Issue 4 (line 31).

- [T118] Review `orchid/mcp/adapter.py` for exactly these 3 issues. For each, report PASS or FAIL with the line number:: Review of `orchid/mcp/adapter.py` — 3 issues:

**Issue 1: No connection state tracking** — The `connect()` method on line 42 calls `self._client.connect()` but does not set a `_connected` flag. Simila

- [T119] Create `tests/test_mcp_types.py`. Write exactly these 3 test functions, no fixtures needed:: Created `tests/test_mcp_types.py` with exactly 3 test functions. All 3 tests pass (3 passed in 0.01s).
</think>

Final Answer: Created `tests/test_mcp_types.py` with exactly 3 test functions — `test_m

- [T120] Create `tests/test_mcp_stdio_client.py`. Write exactly these 4 test functions using `unittest.mock.patch`:: Created `tests/test_mcp_stdio_client.py` with exactly 4 test functions, all using `unittest.mock.patch` to mock `subprocess.Popen`:

1. **`test_connect_success`** — Mocks the subprocess to simulate a

- [T121] Create `tests/test_mcp_http_client.py`. Write exactly these 3 test functions using `respx` to mock httpx:: The `Request` object in respx doesn't have `.json()`. I need to use `.read()` or `.content` and parse it:

Action: write_file
Action Content:
<<<ORCHID
"""Tests for orchid.mcp.http_client — HTTPMCPCli

- [T122] Create `tests/test_mcp_adapter.py`. Write exactly these 4 test functions. Use `unittest.mock.MagicMock` for the client:: File `tests/test_mcp_adapter.py` has been created with 4 test functions covering `connect()`, `list_tools()` caching, `call_tool()` delegation, and `disconnect()` — all passing.

- [T123] Create `tests/test_mcp_manager.py`. Write exactly these 3 test functions using `unittest.mock.patch`:: File created at `tests/test_mcp_manager.py` with 3 test functions, all passing.
