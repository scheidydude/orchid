# Tier 2 Implementation Report (T230-T248)

## Summary

All 19 Tier 2 tasks complete. 15/15 tests passing.

Two critical gaps found during validation (T247) and fixed:
- T234: `BaseAgent._checkpoint_store` attribute not initialized; `set_checkpoint_store()` method missing
- T235: Orchestrator not wiring checkpoint store or `_current_task_id` into agent before run

## Task Results

| Task | Title | Status |
|------|-------|--------|
| T230 | Create `orchid/locks.py` — FileLockRegistry | DONE |
| T231 | Wire FileLockRegistry into write_file/append_file | DONE |
| T232 | Add ReActCheckpoint dataclass to checkpoint/schema.py | DONE |
| T233 | Add save/load_react_checkpoint to checkpoint/store.py | DONE |
| T234 | Save ReAct checkpoint every 5 iterations in BaseAgent | DONE (fixed T247) |
| T235 | Wire checkpoint_store and task_id into agent in orchestrator | DONE (fixed T247) |
| T236 | Create `orchid/mailbox.py` — AgentMailbox | DONE |
| T237 | Add send_message/receive_message tools to BaseAgent | DONE |
| T238 | Drop agent mailbox at task end in orchestrator | DONE |
| T239 | Add agent_id param to bash() in shell.py | DONE |
| T240 | Add agents.max_iterations config to orchid.defaults.yaml | DONE |
| T241 | Read per-agent-type max_iterations hard cap in BaseAgent | DONE |
| T242 | tests/test_file_locks.py — 5 tests | DONE |
| T243 | tests/test_react_checkpoint.py — 3 tests | DONE |
| T244 | tests/test_mailbox.py — 4 tests | DONE |
| T245 | tests/test_shell_agent_id.py — 3 tests | DONE |
| T246 | Review Tier 2 implementation | DONE |
| T247 | Fix issues found in T246 | DONE |
| T248 | Rollup Tier 2 results | DONE |

## Files Created

- `orchid/locks.py` — FileLockRegistry with per-path threading.Lock
- `orchid/mailbox.py` — AgentMailbox with thread-safe queue, get/drop singletons
- `tests/test_file_locks.py`, `tests/test_react_checkpoint.py`, `tests/test_mailbox.py`, `tests/test_shell_agent_id.py`

## Files Extended

- `orchid/tools/filesystem.py` — file-level locking in write_file/append_file
- `orchid/checkpoint/schema.py` — ReActCheckpoint dataclass
- `orchid/checkpoint/store.py` — save_react_checkpoint/load_react_checkpoint
- `orchid/agents/base.py` — checkpoint saving every 5 iters, mailbox tools, max_iterations cap, set_checkpoint_store()
- `orchid/orchestrator.py` — checkpoint store wiring, mailbox cleanup
- `orchid/tools/shell.py` — agent_id param on bash()
- `orchid/orchid.defaults.yaml` — agents.max_iterations config block
