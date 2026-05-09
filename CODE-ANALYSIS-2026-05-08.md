# Code Analysis Report — 2026-05-08

Generated after completing the four-tier Agentic OS gap-closure sprint (T209–T284, commit `b2afaa2`).

---

## Issues and Improvements

### Architectural (non-blocking)

- **`web_server.py:613`** — Web UI has no authentication. Documented as intentional (localhost-only). Required before any networked/team deployment even with `auth` module now present.
- **`gates.py:83`** — TODO: forward `task.stuck` hook notifications to Telegram/Slack (D0020). Watchdog fires the hook but bot notification path is not yet wired.
- **`orchid/auth/middleware.py`** — `_default_store` global singleton lacks thread-safe initialization. Add `threading.Lock` around first-call init to prevent race on concurrent requests.
- **`orchid/auth/store.py`** — `_load()` has no defensive field filtering. Old JSON files missing newer fields will cause `KeyError`. Add `.get()` with defaults for backward compat.
- **`orchid/container_runner.py`** — uses CLI subprocess detection for Docker availability. Consider integrating the `docker` Python library for proper `docker.errors.DockerException` handling.
- **`orchid/interfaces/web/server.py`** — pre-existing `orchid.registry` import error (noted in Tier 3 report, unresolved). Investigate before networked deployment.
- **`orchid/tools/filesystem.py`** — verify `log_file_write()` audit calls are in final committed version. Tier 3 review flagged uncertainty about whether `write_file`/`append_file` actually call it.

### Code Quality

- **15 bare `except Exception` blocks** in integration boundaries (`worker_subprocess.py`, `agent_manager.py`, `tools/auto_review.py`, `tools/filesystem.py`, `planning.py`, `providers/ollama.py`, `lifecycle.py`, `discovery.py`). All wrapped with logging — no silent failures — but worth auditing when adding new fallback paths.
- **`cli.py:459`** — blank `# TODO` comment with no body.

### Test Gaps

- Telegram/Slack bot end-to-end tests minimal (unit coverage exists, no full integration).
- Remote worker end-to-end (dispatcher → worker server round-trip) not yet tested. 3 unit tests cover dispatcher logic only.
- MCP integration: 1 test marked skip (POSIX-only).
- 8 pre-existing test failures: 6 cost ledger `cfg` attribute patching, 2 SearXNG live network tests.

---

## Stale / Obsolete Markdown Files

Safe to delete — review before removing:

| File | Reason |
|------|--------|
| `tier-1-tasks.md` | Sprint complete (T209–T228 done) |
| `tier-2-tasks.md` | Sprint complete (T230–T248 done) |
| `tier-3-tasks.md` | Sprint complete (T249–T265 done) |
| `tier-4-tasks.md` | Sprint complete (T266–T284 done) |
| `phase_1_tasks.md` | 7-phase sprint complete |
| `phase_2_tasks.md` | 7-phase sprint complete |
| `phase_3_tasks.md` | 7-phase sprint complete |
| `phase_4_tasks.md` | 7-phase sprint complete |
| `phase_5_tasks.md` | 7-phase sprint complete |
| `phase_6_tasks.md` | 7-phase sprint complete |
| `phase_7_tasks.md` | 7-phase sprint complete |
| `HEALTH-REPORT.md` | Pre-V2.2, stale (Mar 25) |
| `V2-SUMMARY.md` | Pre-V2.2, superseded by README |
| `REVIEW.md` | Pre-V2.2 architecture review |
| `next_tasks copy.md` | Duplicate of `next_tasks.md` |
| `next_tasks.md` | Superseded by tier/phase task files |
| `todo-next.md` | Scratch notes, superseded |
| `next-phases.md` | Phase outline, all phases complete |
| `HANDOFF-archive-2026-05-06-1400.md` | Timestamped backup; `HANDOFF.md` is current |
| `Orchid-v-Claude-Code-Gap_analysis.md` | Root-level duplicate; `docs/agentic-os-analysis.md` is canonical |
| `docs/chat_note.md` | Minimal/obsolete template |
| `docs/local_chat_template.md` | Minimal/obsolete template |
| `docs/claude_chat_template.md` | Minimal/obsolete template |

---

## Documents Updated

### `README.md`

- Added **"Agentic OS Runtime"** section covering all 19 gap-closure features:
  subprocess isolation, cancellation tokens, wall-clock timeout, stuck-task watchdog,
  dependency cycle detection, file advisory locks, mid-task ReAct checkpointing,
  agent mailbox IPC, shell agent-ID enforcement, max-iterations hard cap,
  capability registry, remote worker protocol, auth layer / per-user quotas,
  container isolation, checkpoint export.
- Updated **Architecture** module table with 15 new files (`subprocess_runner.py`,
  `worker_protocol.py`, `watchdog.py`, `locks.py`, `mailbox.py`, `capability.py`,
  `container_runner.py`, `remote/types.py`, `remote/worker_server.py`,
  `remote/dispatcher.py`, `auth/types.py`, `auth/store.py`, `auth/middleware.py`,
  updated `checkpoint/` and `cost/` entries).
- Test count updated: 1152+ → **1207+**.

### `docs/agentic-os-analysis.md`

- Updated document header to reflect all 19 gaps closed (commit `b2afaa2`).
- Updated concept map table: 4 rows changed from **Missing** → **Implemented** (signal handling, preemption, restart persistence, deadlock detection).
- Added **"Implementation status"** paragraph after each of the 10 gap descriptions with task numbers, files created, and test counts.
- Replaced "Priority Summary" (future plan) with **"Implementation Summary"** (completed checklist) showing all 19 items done with specific file references.

### `docs/DEVELOPMENT-CONTEXT.md`

- Version: **V2.1 → V2.2.4**.
- Test count: **517+ → 1207+** (pre-existing failures noted).
- Project identity paragraph updated with V2.2 summary.
- Key files table: added 15 new module entries.
- Current State: added full **V2.2 Agentic OS gap-closure** feature list (18 bullet points).
- `orchid.defaults.yaml` entry updated to note new `isolation`, `remote`, `agents.max_iterations` config blocks.
