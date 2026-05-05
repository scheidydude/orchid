# Orchid Framework Gap-Closure Report

## Overall Status: **PASSING**
**708 tests pass** (up from 547). All tasks T092–T149 complete. Hook system, MCP adapter, stream-JSON output, session checkpointing, and mobile PWA are fully implemented and verified.

---

## Critical Issues Found
**None.** All safety constraints verified:
- **Deadlock Prevention:** Blocking hooks isolated via `ThreadPoolExecutor` with configurable timeouts (1–300s).
- **Shell Sandboxing:** Shell hooks restricted to built-in allowlist of 50+ safe commands + project-specific allowlist.
- **HTTP Timeouts:** HTTP hooks enforce configurable timeouts (default 10s) via `httpx`.
- **Error Isolation:** Hook errors logged and handled gracefully; never crash agent loop or orchestrator.

---

## Gaps Closed Since Last Report

### 1. Hook System (T092–T104) ✅
Gap closed: *Claude Code has 20+ lifecycle events and 4 hook types. Orchid had none.*

- **Architecture:** `orchid/hooks/` — events, registry, loader, schema, shell/HTTP/Python types.
- **Integration:** Wired into agent ReAct loop, task lifecycle, and session/phase transitions:
  - Agent: `AGENT_ITER_START`, `AGENT_THOUGHT`, `AGENT_ACTION`, `AGENT_OBSERVATION`, `AGENT_FINAL_ANSWER`
  - Task: `TASK_START`, `TASK_COMPLETE`, `TASK_FAILED`, `TASK_BLOCKED`, `TASK_SKIPPED`
  - Session/Phase: `SESSION_START`, `SESSION_END`, `PHASE_TRANSITION`, `PHASE_ENTER`, `PHASE_EXIT`
- **CLI:** `orchid hooks list/show/validate/test/stats/add/remove`
- **Tests:** 69 unit tests + integration tests verify event firing, execution modes, variable substitution, error resilience.

### 2. MCP Adapter Layer (T105–T124) ✅
Gap closed: *Claude Code supports hundreds of MCP servers. Orchid had none.*

- **Types:** `MCPTool`, `MCPResult`, `MCPError` in `orchid/mcp/types.py`.
- **Clients:** `StdioMCPClient` (subprocess.Popen, JSON-RPC 2.0) and `HTTPMCPClient` (httpx sync).
- **Adapter/Manager:** `MCPAdapter` caches tool list; `MCPManager` handles multi-server lifecycle with rollback on failure.
- **Wiring:** `MCPManager` connects before task execution in `orchestrator.py` and `runner.py`; tools injected into agent ReAct loop alongside built-in tools.
- **Config:** `mcp_servers:` section in `.orchid.yaml` / `orchid.defaults.yaml`.
- **CLI:** `orchid mcp ls`, `orchid mcp call`
- **Tests:** `test_mcp_types.py`, `test_mcp_stdio_client.py`, `test_mcp_http_client.py`, `test_mcp_adapter.py`, `test_mcp_manager.py`, `test_mcp_integration.py`

### 3. Stream-JSON Output (T125–T137) ✅
Gap closed: *Orchid had no structured output mode for programmatic consumption.*

- **Events:** Typed dataclasses in `orchid/output/events.py` — `SessionStartEvent`, `TaskStartEvent`, `AgentThoughtEvent`, `ToolUseEvent`, `ToolResultEvent`, `TaskCompleteEvent`, `TaskBlockedEvent`, `SessionEndEvent`.
- **Emitters:** `NDJSONEmitter` (stream), `NDJSONBufferEmitter` (in-memory), `WebSocketEmitter`.
- **CLI:** `--output-format stream-json` on `orchid --mode auto`.
- **Web endpoint:** `GET /api/projects/{id}/stream` returns NDJSON event stream.
- **Tests:** `test_output_events.py`, `test_ndjson_emitter.py`, `test_stream_json_cli.py`

### 4. Session Checkpointing (T138–T149) ✅
Gap closed: *Claude Code has `/rewind` and session branching. Orchid had no rollback.*

- **Schema:** `CheckpointMetadata`, `Checkpoint`, `CheckpointEntry` in `orchid/checkpoint/schema.py`.
- **Store:** `CheckpointStore` — save, load, list, delete, prune (keep=5).
- **Restore:** `rewind_session()`, `list_checkpoints()` in `orchid/checkpoint/restore.py`.
- **Wiring:** Checkpoint captured before each task in `orchestrator.py`; pruned in `runner.py` finally block.
- **CLI:** `--list-checkpoints`, `--rewind CHECKPOINT_ID`, `--resume CHECKPOINT_ID`
- **Tests:** `test_checkpoint_schema.py`, `test_checkpoint_store.py`, `test_checkpoint_restore.py`, `test_checkpoint_integration.py`

### 5. Mobile PWA Web UI ✅
Gap closed: *Claude Code has iOS/Android Dispatch app. Orchid web UI was desktop-only.*

- **Phase 1:** Responsive CSS — fluid grid, breakpoints, stacked layouts on small screens.
- **Phase 2:** Mobile navigation — hamburger drawer, short tab labels, touch-friendly tap targets.
- **Phase 3:** Touch adaptation — swipe gestures, scroll reset on tab change, larger hit areas.
- **Phase 4:** PM visualizations adapted — charts and dependency graph scale to mobile viewport.
- **Phase 5:** PWA polish — `manifest.json`, safe-area insets, `no-cache` headers on `index.html`, decoupled mobile layout from JS `isMobile` state.
- **Bug fix:** Task Timing deduplicates `task_metrics.jsonl` by `task_id` (last entry wins) so re-run tasks show current status instead of stale BLOCKED status.

---

## Remaining Gaps vs Claude Code

| Dimension | Status |
|-----------|--------|
| IDE integration (VS Code, JetBrains) | Not planned — different use case |
| Skill/plugin marketplace | Not planned |
| Model effort levels (low/medium/high/max) | Not planned |
| Native git integration (PRs, branches) | Partial — via bash tool |
| Browser automation (`--chrome`) | Not planned |
| Organization/IT managed settings | Not planned |
| Background scheduling on Anthropic infra | N/A — Orchid is self-hosted |
| Subagent worktree isolation | Open — delegation depth 3, no worktree |

---

## Test Summary

| Suite | Count |
|-------|-------|
| Hook system (unit + integration) | ~75 |
| MCP adapter layer | ~15 |
| Stream-JSON output | ~10 |
| Session checkpointing | ~12 |
| All other (orchestrator, agents, providers, CLI, web) | ~596 |
| **Total (non-network)** | **708** |

---

## Recommended Next Steps

1. **Subagent worktree isolation** — delegate sub-tasks to isolated working directories to match Claude Code's worktree model.
2. **Native git integration** — commit, branch, PR operations as first-class tools rather than bash passthrough.
3. **Circuit breakers for HTTP hooks** — suggested in T100 security review; implement for production hardening.
4. **Audit logging for shell hooks** — log command, exit code, output to `.orchid/hook_audit.jsonl`.
