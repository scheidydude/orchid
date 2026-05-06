# HANDOFF.md
_Written: 2026-05-05. Fresh session should read this + the repo; ask nothing that's answered here._

---

## 1. Mission

Orchid is a standalone AI agent orchestration framework that runs multi-agent pipelines over external projects. We're executing a structured 7-phase improvement sprint: security hardening → native git → worktree isolation → parallelism → dynamic spawning → cross-project agent pool → cost scheduling. The sprint was designed this session; Phase 1 is now complete.

---

## 2. Current State

### What's working and verified
- **Phase 1 complete. 833 tests pass (`source .venv/bin/activate && python -m pytest tests/ -q`).**
- Circuit breaker (`orchid/hooks/circuit_breaker.py`) — event-level state machine, 55 tests pass.
- Audit logger (`orchid/hooks/audit.py`) — writes `.orchid/audit_log.jsonl` per hook invocation, 60 tests pass.
- Both wired into `orchid/hooks/loader.py` via `_configure_circuit_breaker()` and `_configure_audit_logger()`.
- Per-agent tool restrictions: `BaseAgent.allowed_tools` + `__init__` filter. TesterAgent/ReviewerAgent cannot call `write_file`/`append_file`. ResearcherAgent restricted to read/search/fetch. DeveloperAgent unrestricted. Config override via `agents.allowed_tools.<type>` in `.orchid.yaml`. 4 permission tests pass.

### What's uncommitted (IMMEDIATE ACTION REQUIRED)
`git status` shows Phase 1 Orchid-created files sitting unstaged:
```
modified:   CLAUDE.md                     ← Orchid updated its own context
modified:   orchid/hooks/loader.py        ← circuit breaker + audit wiring
modified:   orchid/orchid.defaults.yaml   ← allowed_tools docs + circuit_breaker config
modified:   tasks.md                      ← Phase 1 tasks marked [x]
untracked:  orchid/hooks/audit.py
untracked:  orchid/hooks/circuit_breaker.py
untracked:  tests/test_circuit_breaker.py
untracked:  tests/test_hook_audit.py
```
**Do NOT stage `.claude/settings.local.json`** — that's local harness config.

Commit command:
```bash
git add orchid/hooks/audit.py orchid/hooks/circuit_breaker.py \
        orchid/hooks/loader.py orchid/orchid.defaults.yaml \
        CLAUDE.md tasks.md \
        tests/test_circuit_breaker.py tests/test_hook_audit.py
git commit -m "feat(hooks): Phase 1 — circuit breaker, audit logging, wired into loader"
git push
```

### Phase 2-7: planned, not started
Task files `phase_2_tasks.md` through `phase_7_tasks.md` exist in the repo root. Committed at `1334115`. These are copied into `tasks.md` one phase at a time before running Orchid. Phase 2 is next.

### Next action (after committing above)
1. Optionally deploy Phase 1 to server (it's backward-compatible, safe to ship).
2. Copy Phase 2 tasks into `tasks.md`, run `orchid --project . --mode auto`.
3. After Phase 2 runs, validate with `pytest tests/test_git_tools.py` + `grep` checks.

---

## 3. Decisions Made (and Why)

**Decision:** Per-agent `allowed_tools` as frozenset class variable + `__init__` filter, overridable via YAML.
- **Alternatives:** Purely config-driven (no class defaults); purely hardcoded (no YAML override).
- **Reason:** Class defaults give sensible out-of-box security; YAML override lets project-specific configs loosen constraints without touching source.
- **Reversibility:** Easy to change frozensets or remove filter entirely.

**Decision:** `ResearcherAgent.allowed_tools` includes `search` and `fetch` even though they're not in `self.tools` when the filter runs in `BaseAgent.__init__`.
- **Reason:** `ResearcherAgent.__init__` calls `register_tool("search", ...)` *after* `super().__init__()`. The filter removes tools present-but-not-allowed; tools not yet registered simply aren't touched. They're added afterward and bypass the filter. This is correct: search/fetch ARE allowed, and they can't be added by a bad actor via `register_tool` on TesterAgent (no code does that).
- **Reversibility:** If we want stricter enforcement, move filter to `_apply_tool_restrictions()` called at the end of each subclass `__init__`. T161 review flagged this; T162 has the fix template.

**Decision:** Mandatory `bash grep` verification step added to every task that edits `base.py` or `orchestrator.py`.
- **Reason:** T155/T156/T160 failure post-mortem: local model read those files, echoed raw code as Final Answer, and Orchid marked tasks DONE without any changes being made. Grep verification forces the model to confirm the symbol is actually on disk.
- **Reversibility:** Cosmetic; doesn't affect runtime.

**Decision:** Multi-file tasks (T188: base.py + orchestrator.py; T203/T203b; T166/T166b) split into single-file tasks.
- **Reason:** Same failure mode — model gets confused on large files and produces garbage output for one of the files.
- **Reversibility:** If Claude-class model handles them, the tasks can be merged. For local model, keep split.

**Decision:** Circuit breaker implemented by Orchid as an event-level registry (`CircuitBreakerRegistry` singleton with per-event-type breakers) rather than the per-hook-name design specified in `phase_1_tasks.md`.
- **Reason:** Orchid's local model chose a richer design autonomously. Tests pass, functionality correct. The loader wires it via `configure_circuit_breaker(config)` at load time.
- **Reversibility:** The task spec was advisory; the implementation is load-bearing. Don't refactor unless there's a specific gap.

**Decision:** Audit log file is `.orchid/audit_log.jsonl` (Orchid chose this) vs `.orchid/hook_audit.jsonl` (spec said this).
- **Reason:** Orchid autonomously chose the name. Tests pass against the actual path. No consumer reads this file yet.
- **Reversibility:** Trivial rename if standardization matters later.

**Decision:** Phase ordering: security → git → worktrees → parallelism → spawning → pool → cost.
- **Reason:** Documented in `next-phases.md`. Short version: harden before expand; git before worktrees (worktrees are git); isolation before parallelism (parallel agents need separate trees); parallelism before dynamic spawning (spawning needs the dispatch layer).
- **Reversibility:** Each phase is independent enough to skip or reorder. Phase 4 is highest risk.

---

## 4. Architecture & Key Files

### Created this session
| File | Purpose |
|------|---------|
| `phase_1_tasks.md` – `phase_7_tasks.md` | Sprint task lists T151–T208+. Copy one phase at a time into `tasks.md`. |
| `next-phases.md` | Rationale for phase ordering. Reference doc, not executable. |
| `todo-next.md` | Original backlog items that drove the 7 phases. |
| `orchid/hooks/circuit_breaker.py` | Event-level circuit breaker. `CircuitBreakerRegistry` singleton, `CircuitBreakerConfig` dataclass, `configure_circuit_breaker()` fn. |
| `orchid/hooks/audit.py` | `AuditLogger` singleton. Writes `.orchid/audit_log.jsonl`. `configure_audit_logger(project_dir)` wires it. |
| `tests/test_circuit_breaker.py` | 55 tests for circuit breaker state machine. |
| `tests/test_hook_audit.py` | 60 tests for audit logger. |
| `tests/test_agent_permissions.py` | 4 tests verifying allowed_tools filtering. |

### Modified this session
| File | What changed |
|------|-------------|
| `orchid/hooks/loader.py` | Added `_configure_circuit_breaker()` and `_configure_audit_logger()` called from `load()`. Also Orchid expanded `_create_http_handler` and `_create_shell_handler` with timing/status tracking. |
| `orchid/orchid.defaults.yaml` | Added `allowed_tools` documentation (commented examples) and `allowed_tools: {}` under `agents:`. Added `circuit_breaker:` config block under `hooks:`. |
| `orchid/agents/base.py` | Added `allowed_tools: frozenset[str] \| None = None` class var. Added filter block at end of `__init__`. |
| `orchid/agents/tester.py` | Added `allowed_tools` frozenset: `{read_file, list_dir, bash, check_imports, get_task_files}`. |
| `orchid/agents/reviewer.py` | Added `allowed_tools` frozenset: same as tester. |
| `orchid/agents/researcher.py` | Added `allowed_tools` frozenset: `{read_file, list_dir, bash, search, fetch, get_task_files}`. |
| `tasks.md` | Phase 1 tasks T151–T162 marked `[x]`. |
| `CLAUDE.md` | Orchid compressed and updated its own context file. |

### Do not touch
- `orchid/agents/developer.py` — no `allowed_tools` by design (DeveloperAgent is unrestricted). Don't add one unless explicitly asked.
- `orchid/hooks/circuit_breaker.py` and `orchid/hooks/audit.py` — Orchid's richer implementation supersedes the spec. Don't refactor to match the spec.

---

## 5. Gotchas & Hard-Won Knowledge

**Local model echoes file contents as Final Answer.** T155, T156, T160 were marked DONE in `tasks.md` with results containing raw code fragments from the files the model read — zero actual changes on disk. The model ran, read `base.py`, output its contents as Final Answer, Orchid marked DONE. **Mitigation:** every base.py/orchestrator.py edit task now ends with a mandatory `bash grep` verification step. If the model doesn't find its own symbol after "writing," it retried.

**`task_results.json` is NDJSON, not JSON.** `json.load()` fails. Use `json.loads(line)` per line.

**`source .venv/bin/activate` required before `pytest`.** `python` is not on PATH; only `python3`, and the venv python is the one with test deps. Always run `source .venv/bin/activate && python -m pytest`.

**ResearcherAgent tool filter timing.** The `allowed_tools` filter runs at end of `BaseAgent.__init__`. `ResearcherAgent` registers `search` and `fetch` in its own `__init__` *after* calling `super().__init__()`. This means `search`/`fetch` are added after the filter runs and are never filtered out — which is the correct behavior since they ARE in `ResearcherAgent.allowed_tools`. If you ever add an `_apply_tool_restrictions()` call to subclasses, be aware of this ordering.

**T161 review identified the filter timing as Issue 5.** It was marked PASS (not a problem) because the behavior is correct. T162 exists as a fix task if it's ever re-evaluated as a FAIL.

**Phase 1 Orchid-created files are NOT committed.** After this session they're sitting unstaged. See Current State section for exact commit command.

**Audit log path mismatch.** Spec said `.orchid/hook_audit.jsonl`; Orchid built `.orchid/audit_log.jsonl`. Tests cover the actual path. Don't create a second file.

**`needs:` deps on cross-phase tasks.** T165 in Phase 2 has `needs:T156` (Phase 1). When Phase 2 tasks are appended to `tasks.md`, T156 is already there as `[x]`. The `is_runnable()` check correctly sees it as completed. Don't remove this dep thinking it's stale.

---

## 6. Conventions In Play

- **Task format:** `- [ ] **T001** Title \`type:code_generate\` \`p1\` \`needs:T002\` \`model:local\``
- **Model routing:** `model:local` → DeveloperAgent (local LLM); `type:code_review` → ReviewerAgent (Claude). No annotation → default routing per `orchid.defaults.yaml`.
- **Commit style:** conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`), co-authored with Claude.
- **No comments in code** unless the WHY is non-obvious. No multi-line docstrings.
- **Task descriptions** must be prescriptive enough that the local model makes zero decisions: exact file paths, class names, method signatures, field names, and a grep verification step for edits to large files.
- **Single-file per task** for `orchid/agents/base.py` and `orchid/orchestrator.py` edits. These files are large enough that the local model gets confused on multi-file tasks.
- **Phase files are read-only once execution starts.** Don't edit `phase_N_tasks.md` after copying into `tasks.md` — the tasks.md copy is the live version.

---

## 7. Open Questions

None deferred from this session. The phase files are complete through Phase 7. When Phase 2 runs, the review task (T168) may produce FAILs that generate concrete questions — those will appear in `task_results.json`.

---

## 8. Do Not Touch

- **`orchid/agents/developer.py`** — no `allowed_tools`. DeveloperAgent is intentionally unrestricted.
- **`orchid/hooks/circuit_breaker.py`** — Orchid's implementation is richer than the spec and all tests pass. Don't refactor to match `phase_1_tasks.md` spec.
- **`orchid/hooks/audit.py`** — same. Audit log path is `.orchid/audit_log.jsonl`, not `hook_audit.jsonl`.
- **`next-phases.md`** — rationale doc, not executable. Don't modify.
- **Phase 1 `needs:` dependencies in Phase 2–7 task files** — e.g. T165 `needs:T156`. These cross-phase deps are intentional and correct.
- **The 7-phase ordering** — settled in `next-phases.md`. Don't re-derive unless the user explicitly wants to revisit.

---

## 9. Resume Command

> Read `HANDOFF.md`. First action: commit the uncommitted Phase 1 files exactly as specified in §2 (do NOT stage `.claude/settings.local.json`). Run `source .venv/bin/activate && python -m pytest tests/ -q --tb=no` to confirm 833 pass. Then ask the user: deploy Phase 1 now, or proceed directly to Phase 2? For Phase 2, copy `phase_2_tasks.md` contents into `tasks.md` and run `orchid --project . --mode auto`. Do not modify any `phase_N_tasks.md` file directly.
