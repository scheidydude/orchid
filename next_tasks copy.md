# Next Tasks — Gap Closure Sprint

Source: `Orchid-v-Claude-Code-Gap_analysis.md`  
Scope: Hook system · MCP adapter layer · Stream-JSON output · Session checkpointing  
Format: Orchid `tasks.md` convention. Tasks start at T092.

---

## Feature 1: Hook System

Close gap: *Claude Code has 20+ lifecycle events, 4 hook types. Orchid has none.*  
Goal: fire hooks at PreToolUse, PostToolUse, TaskStart, TaskComplete, SessionStart, SessionStop, PhaseTransition. Support blocking and non-blocking hooks. Load from `.orchid.yaml` and per-project `hooks.py`.

### Code Generation

- [ ] **T092** Design and implement `orchid/hooks/registry.py`: `HookRegistry` class with `register(event, handler, blocking=False)`, `fire(event, context) -> HookResult`. `HookResult` carries `blocked: bool`, `mutated_context: dict | None`, `error: str | None`. Blocking hooks that return `blocked=True` halt the caller. Non-blocking hooks run in a thread pool and their results are logged but not awaited by the caller. `type:code_generate` `p1`

- [ ] **T093** Define hook event constants in `orchid/hooks/events.py`: `PRE_TOOL_USE`, `POST_TOOL_USE`, `TASK_START`, `TASK_COMPLETE`, `TASK_FAIL`, `SESSION_START`, `SESSION_END`, `PHASE_TRANSITION`, `DELEGATION_START`, `DELEGATION_END`. Each event has a typed `HookContext` dataclass with relevant fields (task_id, tool_name, action_input, result, phase, etc.). `type:code_generate` `p1` `needs:T092`

- [ ] **T094** Implement hook loader `orchid/hooks/loader.py`: reads `hooks:` section from `.orchid.yaml`. Supports two hook kinds: (1) `shell` — runs a bash command, receives context as JSON on stdin, reads mutated context or `{"block": true}` from stdout. (2) `http` — POSTs context JSON to a URL, reads response. Also loads `hooks.py` from project root if present and registers any functions decorated with `@orchid_hook(event)`. `type:code_generate` `p1` `needs:T093`

- [ ] **T095** Wire hooks into agent ReAct loop `orchid/agent.py`: fire `PRE_TOOL_USE` before executing any Action, pass `{tool, input}` as context. If hook returns `blocked=True`, skip tool execution and inject a synthetic Observation: `"[BLOCKED by hook: <reason>]"`. Fire `POST_TOOL_USE` after each Observation with `{tool, input, output}`. `type:code_generate` `p1` `needs:T094`

- [ ] **T096** Wire hooks into task lifecycle `orchid/orchestrator.py`: fire `TASK_START` before a task begins execution with `{task_id, task_title, task_type}`. Fire `TASK_COMPLETE` on success with `{task_id, duration_s, iterations}`. Fire `TASK_FAIL` on exception with `{task_id, error}`. `type:code_generate` `p1` `needs:T094`

- [ ] **T097** Wire hooks into session and phase transitions: fire `SESSION_START` in `orchid/runner.py` at run start with `{project, mode, provider}`. Fire `SESSION_END` at run end with `{task_count, duration_s}`. Fire `PHASE_TRANSITION` in `orchid/lifecycle.py` when phase advances with `{from_phase, to_phase, project}`. `type:code_generate` `p1` `needs:T094`

- [ ] **T098** Add hook config schema to `.orchid.yaml` and `orchid/config.py`: extend `OrchidConfig` to parse `hooks:` list. Each entry: `{event, kind, command|url, blocking, timeout_s}`. Add `hooks: []` with examples to `orchid/defaults/orchid.defaults.yaml`. `type:code_generate` `p1` `needs:T094`

- [ ] **T099** Add CLI: `orchid hooks list --project .` prints all registered hooks (event, kind, blocking, source). `orchid hooks test <event> --project .` fires the event with a synthetic context and prints the result. `type:code_generate` `p2` `needs:T098`

### Review

- [ ] **T100** Review hook registry and loader implementation: verify blocking hooks cannot deadlock the orchestrator, shell hooks are sandboxed by the existing shell allowlist, http hooks respect timeout, and hook errors are logged but never crash the agent loop. Check `loader.py` for arbitrary code injection via `hooks.py` imports. `type:review` `p1` `needs:T094,T095,T096,T097`

- [ ] **T101** Review hook integration points in `agent.py` and `orchestrator.py`: confirm context objects do not leak secrets (API keys, full file contents), PRE_TOOL_USE context is immutable by default, and mutation is opt-in only for designated mutable fields. `type:review` `p1` `needs:T095,T096`

### Testing

- [ ] **T102** Unit tests `tests/test_hooks_registry.py`: test register/fire blocking hook blocks caller, non-blocking hook runs async and does not block, multiple hooks for same event fire in registration order, exception in hook does not propagate to caller. `type:test_write` `p1` `needs:T092,T093`

- [ ] **T103** Unit tests `tests/test_hooks_loader.py`: test shell hook fires and reads stdout JSON, shell hook `{"block": true}` propagates blocked flag, http hook POSTs correct payload, invalid hooks.py import raises `HookLoadError` not crash. `type:test_write` `p1` `needs:T094`

- [ ] **T104** Integration tests `tests/test_hooks_integration.py`: run a minimal agent session with a shell hook on `PRE_TOOL_USE` that blocks `bash` actions — verify agent receives blocked observation and does not execute bash. Run session with `TASK_COMPLETE` hook that appends to a log file — verify file is written after task completes. `type:test_write` `p1` `needs:T095,T096,T097`

---

## Feature 2: MCP Adapter Layer

Close gap: *Claude Code supports hundreds of MCP servers. Orchid has no MCP support.*  
Goal: connect to MCP servers (stdio or HTTP/SSE), expose their tools as Orchid ReAct actions, configure servers in `.orchid.yaml`.

### Code Generation

- [ ] **T105** Implement `orchid/mcp/client.py`: `MCPClient` base class. `StdioMCPClient(command, args, env)` starts server process, communicates via JSON-RPC over stdin/stdout per MCP spec. `HttpMCPClient(url, headers)` uses HTTP + Server-Sent Events transport. Both implement `list_tools() -> list[MCPTool]` and `call_tool(name, arguments) -> MCPResult`. `type:code_generate` `p1`

- [ ] **T106** Implement `orchid/mcp/adapter.py`: `MCPToolAdapter` wraps an `MCPClient` and registers each discovered tool as an Orchid ReAct action. Tool names namespaced as `mcp__<server_name>__<tool_name>` matching Claude Code convention. Adapter injects tools into the agent's available actions list at session start. `type:code_generate` `p1` `needs:T105`

- [ ] **T107** Implement `orchid/mcp/manager.py`: `MCPManager` reads `mcp_servers:` from `.orchid.yaml`, starts/stops server processes, maintains adapter registry. Provides `get_adapters() -> list[MCPToolAdapter]`. Handles server crash + restart with exponential backoff. `type:code_generate` `p1` `needs:T106`

- [ ] **T108** Wire `MCPManager` into agent session startup `orchid/runner.py`: at session start, `MCPManager.start_all()`, inject adapter tools into agent tool registry. At session end, `MCPManager.stop_all()`. Log discovered MCP tools at DEBUG level. `type:code_generate` `p1` `needs:T107`

- [ ] **T109** Add MCP config schema to `.orchid.yaml` and `orchid/config.py`: extend `OrchidConfig` with `mcp_servers: list[MCPServerConfig]`. `MCPServerConfig`: `{name, kind: stdio|http, command, args, env, url, headers, enabled, allowed_tools: list[str] | null}`. `allowed_tools: null` means all tools permitted; list restricts which tools are exposed to the agent. Add examples to `orchid.defaults.yaml`. `type:code_generate` `p1` `needs:T107`

- [ ] **T110** Add permission filtering for MCP tools in `orchid/mcp/adapter.py`: respect `allowed_tools` list from config. Also check tool name against Orchid shell allowlist — MCP tools that execute shell commands are treated as `bash` actions for allowlist purposes. `type:code_generate` `p2` `needs:T109`

- [ ] **T111** Add CLI: `orchid mcp list --project .` prints all configured MCP servers and their discovered tools. `orchid mcp test <server_name> --project .` starts the server, calls `list_tools()`, prints result, shuts down. `type:code_generate` `p2` `needs:T107`

### Review

- [ ] **T112** Review `MCPClient` implementations: verify stdio client does not allow shell injection via tool arguments, HTTP client enforces timeout and does not follow redirects to localhost (SSRF), and server crash does not propagate exception into agent loop. Confirm JSON-RPC message framing is correct per MCP spec. `type:review` `p1` `needs:T105,T106,T107`

- [ ] **T113** Review MCP tool injection into agent: verify tool names are sanitized before insertion into ReAct prompt, tool descriptions from MCP server are truncated to prevent prompt injection, and `allowed_tools` filtering is applied before tools reach the agent. `type:review` `p1` `needs:T108,T110`

### Testing

- [ ] **T114** Unit tests `tests/test_mcp_client.py`: mock subprocess for `StdioMCPClient` — test `list_tools()` parses JSON-RPC response, `call_tool()` sends correct request, server exit raises `MCPClientError`. Mock httpx for `HttpMCPClient` — test SSE streaming parses tool result. `type:test_write` `p1` `needs:T105`

- [ ] **T115** Unit tests `tests/test_mcp_adapter.py`: test adapter registers tools with correct namespaced names, `allowed_tools` filter excludes unlisted tools, adapter call returns MCPResult wrapped as Orchid observation string. `type:test_write` `p1` `needs:T106,T110`

- [ ] **T116** Integration tests `tests/test_mcp_integration.py`: implement a minimal in-process MCP test server (echo tools only), start it via `MCPManager`, run a minimal agent task that calls an MCP tool, verify the observation contains the tool result. `type:test_write` `p1` `needs:T107,T108`

---

## Feature 3: Stream-JSON Output

Close gap: *Claude Code has `--output-format stream-json` for CI/programmatic use. Orchid has no machine-readable output mode.*  
Goal: `--output-format stream-json` on CLI emits newline-delimited JSON events. FastAPI `/run` endpoint supports `Accept: application/x-ndjson`. Enables external tooling to consume Orchid output.

### Code Generation

- [ ] **T117** Define stream-json event schema `orchid/output/events.py`: dataclasses for `SessionStart`, `TaskStart`, `AgentThought`, `ToolUse`, `ToolResult`, `TaskComplete`, `TaskFail`, `SessionEnd`. All inherit from `StreamEvent(type: str, ts: float, session_id: str)`. Each serializes to a flat JSON object with `type` field as discriminator. `type:code_generate` `p1`

- [ ] **T118** Implement `orchid/output/emitter.py`: `StreamEmitter` with `emit(event: StreamEvent)` method. Two implementations: `NdJsonEmitter` writes `json.dumps(event) + "\n"` to a file-like (stdout or file). `WebSocketEmitter` sends to existing WS connection (reuse existing WS infrastructure). `NullEmitter` discards all events (default when no output format set). `type:code_generate` `p1` `needs:T117`

- [ ] **T119** Wire `StreamEmitter` into agent ReAct loop `orchid/agent.py`: emit `AgentThought` on each Thought line, `ToolUse` before each action, `ToolResult` after each observation. Emitter instance injected at construction time — no direct import of emitter in agent. `type:code_generate` `p1` `needs:T118`

- [ ] **T120** Wire `StreamEmitter` into task and session lifecycle `orchid/runner.py` and `orchid/orchestrator.py`: emit `SessionStart` at run start, `TaskStart`/`TaskComplete`/`TaskFail` per task, `SessionEnd` at run end with summary stats. `type:code_generate` `p1` `needs:T118`

- [ ] **T121** Add `--output-format text|json|stream-json` flag to CLI `orchid/__main__.py`: `text` = current behavior. `json` = emit single JSON object at end with session summary. `stream-json` = instantiate `NdJsonEmitter(sys.stdout)` and inject into runner. When `stream-json` active, suppress all other stdout logging (INFO/DEBUG) — only JSON lines go to stdout; logs go to stderr. `type:code_generate` `p1` `needs:T118,T119,T120`

- [ ] **T122** Add NDJSON streaming to FastAPI `/run` endpoint `orchid/server/routes/run.py`: when `Accept: application/x-ndjson` header present (or `?stream=true` param), return `StreamingResponse` with `media_type="application/x-ndjson"`. Use `WebSocketEmitter` pattern adapted for HTTP chunked transfer. `type:code_generate` `p2` `needs:T118,T119,T120`

### Review

- [ ] **T123** Review stream-json event schema: verify all events include `session_id` and `ts`, sensitive fields (file contents, API keys) are never included in `ToolUse`/`ToolResult` events, and schema is stable enough for external consumers to rely on. Check that `NdJsonEmitter` flushes after each line so consumers receive events in real time. `type:review` `p1` `needs:T117,T118`

- [ ] **T124** Review CLI flag wiring: confirm `--output-format stream-json` suppresses non-JSON output on stdout, logger handlers are redirected to stderr only, and the flag is correctly excluded from `--help` text as experimental if not yet stable. `type:review` `p1` `needs:T121`

### Testing

- [ ] **T125** Unit tests `tests/test_output_emitter.py`: test `NdJsonEmitter` writes valid JSON lines, each line parses to dict with `type` and `ts` fields, `NullEmitter` silently discards events, emitter exception does not propagate to agent. `type:test_write` `p1` `needs:T118`

- [ ] **T126** Integration tests `tests/test_stream_json.py`: run minimal agent task with `--output-format stream-json`, capture stdout, parse each line as JSON, assert event sequence includes `session_start → task_start → tool_use → tool_result → task_complete → session_end`. Verify stderr contains log lines and stdout contains only JSON. `type:test_write` `p1` `needs:T119,T120,T121`

---

## Feature 4: Session Checkpointing

Close gap: *Claude Code has `/rewind` and session branching. Orchid has no session undo or crash recovery.*  
Goal: checkpoint task state before each execution. Support `--rewind <task_id>` to restore pre-task state. Support `--resume` to continue after crash. Keep last N checkpoints per task.

### Code Generation

- [ ] **T127** Define checkpoint schema `orchid/checkpoint/schema.py`: `Checkpoint` dataclass with `session_id`, `task_id`, `created_at`, `tasks_state: list[TaskSnapshot]` (id, status, result snippet), `agent_messages: list[dict]` (last N messages from context window), `files_modified: list[FileSnapshot]` (path + sha256 before-edit). `FileSnapshot` stores the file content at checkpoint time for files touched in the session so far. `type:code_generate` `p1`

- [ ] **T128** Implement `orchid/checkpoint/store.py`: `CheckpointStore` reads/writes checkpoints to `.orchid/checkpoints/<session_id>/<task_id>.json`. `save(checkpoint)` serializes to JSON. `load(session_id, task_id) -> Checkpoint`. `list_checkpoints(session_id) -> list[str]`. `prune(session_id, keep_last=10)` deletes oldest beyond limit. `type:code_generate` `p1` `needs:T127`

- [ ] **T129** Implement checkpoint capture in `orchid/orchestrator.py`: before executing each task, call `CheckpointStore.save(checkpoint)` where `checkpoint` captures current `tasks.md` state (all task statuses), last 20 agent messages from context, and sha256 of every file modified since session start. Run capture in a thread to avoid blocking task start. `type:code_generate` `p1` `needs:T128`

- [ ] **T130** Implement checkpoint restore `orchid/checkpoint/restore.py`: `restore(session_id, task_id, project_path)` loads checkpoint, rewrites `tasks.md` to snapshot state (re-opens completed tasks after the target task), restores file contents from `FileSnapshot` entries, returns restored `Checkpoint`. Dry-run mode (`--dry-run`) prints what would change without writing. `type:code_generate` `p1` `needs:T128`

- [ ] **T131** Add CLI flags to `orchid/__main__.py`: `orchid --rewind <task_id> --project .` calls `restore()`, prints summary of restored files and task states, prompts confirmation before writing (bypass with `--yes`). `orchid --resume --project .` finds the latest checkpoint for the current session and re-runs the orchestrator from that point, skipping already-completed tasks. `orchid --list-checkpoints --project .` prints checkpoint table (task_id, created_at, files_modified count). `type:code_generate` `p1` `needs:T129,T130`

- [ ] **T132** Add checkpoint pruning to session teardown `orchid/runner.py`: at `SESSION_END`, call `CheckpointStore.prune(session_id, keep_last=10)`. Make `keep_last` configurable via `.orchid.yaml` `checkpointing.keep_last` (default 10). `type:code_generate` `p2` `needs:T129`

### Review

- [ ] **T133** Review checkpoint schema and restore logic: verify `FileSnapshot` stores content not just sha256 (content needed for restore), restore does not overwrite files with unsaved edits made outside of Orchid, `tasks.md` rewrite preserves non-status fields (titles, types, priorities), and restore is atomic (write to temp file then rename). `type:review` `p1` `needs:T127,T128,T130`

- [ ] **T134** Review CLI `--rewind` UX: confirm confirmation prompt is shown by default, `--yes` bypass is documented, dry-run output is human-readable and accurate, and rewind of a task with dependents warns the user that dependent task results will also be invalidated. `type:review` `p1` `needs:T131`

### Testing

- [ ] **T135** Unit tests `tests/test_checkpoint_store.py`: test save/load round-trip preserves all fields, `prune(keep_last=3)` deletes correct entries, `load` on missing checkpoint raises `CheckpointNotFound`, file with sha256 mismatch at restore time logs warning. `type:test_write` `p1` `needs:T127,T128`

- [ ] **T136** Integration tests `tests/test_checkpoint_integration.py`: run two tasks in sequence, verify checkpoint files exist in `.orchid/checkpoints/` after each, call `restore()` for task 2 checkpoint, verify `tasks.md` and file contents match snapshot. Simulate crash after task 1 completes (raise in task 2 setup), call `--resume`, verify task 2 re-runs and completes. `type:test_write` `p1` `needs:T129,T130,T131`

---

## Rollup

- [ ] **T137** Gap-closure sprint rollup `type:rollup` `p1` `rollup:T092,T093,T094,T095,T096,T097,T098,T099,T100,T101,T102,T103,T104,T105,T106,T107,T108,T109,T110,T111,T112,T113,T114,T115,T116,T117,T118,T119,T120,T121,T122,T123,T124,T125,T126,T127,T128,T129,T130,T131,T132,T133,T134,T135,T136` `output:GAP-CLOSURE-REPORT.md`

---

## Summary

| Feature | Code tasks | Review tasks | Test tasks | Total |
|---------|-----------|-------------|-----------|-------|
| Hook system | T092–T099 (8) | T100–T101 (2) | T102–T104 (3) | 13 |
| MCP adapter | T105–T111 (7) | T112–T113 (2) | T114–T116 (3) | 12 |
| Stream-JSON output | T117–T122 (6) | T123–T124 (2) | T125–T126 (2) | 10 |
| Session checkpointing | T127–T132 (6) | T133–T134 (2) | T135–T136 (2) | 10 |
| **Total** | **27** | **8** | **10** | **45** |

Suggested execution order: T092→T104 (hooks, unblocked), then T105→T116 (MCP, depends on hooks being stable for T110), T117→T126 (stream-json, independent), T127→T136 (checkpointing, independent). All four tracks can run in parallel once hooks core (T092–T094) is done.
