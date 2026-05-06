# Orchid — Next Phases

Derived from `todo-next.md`. Order prioritizes: (1) harden what exists before expanding it, (2) build foundations before features that depend on them, (3) defer architectural complexity until simpler layers are stable.

---

## Phase 1 — Security Hardening

**Items:** Circuit breakers for HTTP hooks · Audit logging for shell hooks · Sandboxing / permissions model (capability-scoped tools per agent)

**Why first:**
The hooks system (T092–T104) and shell allowlist (T051) shipped without production hardening. Circuit breakers prevent a misbehaving HTTP endpoint from hanging the agent loop. Audit logging produces the operational record that later phases (cost scheduling, debugging parallel runs) will depend on. The permissions model caps blast radius before we expand agent surface area — a TesterAgent that can't write arbitrary files is safe to run in parallel or in a shared pool.

All three are *constraint* changes, not *capability* changes. They tighten existing code rather than adding new call paths, so risk is low and the benefit compounds across every phase that follows.

---

## Phase 2 — Native Git Integration

**Items:** Native git integration (commit, branch, PR as first-class tools)

**Why second:**
Everything that follows — worktree isolation, parallel branches, dynamic subagent spawning — is a git concept. Right now `git` reaches the agent only through `bash` passthrough, which bypasses the shell allowlist semantics and produces unstructured output. First-class git tools give agents structured return values (sha, branch name, PR URL) that downstream logic can act on, and they let the allowlist enforce which git operations each agent class may run (tester: read-only; developer: commit + push).

This is a contained addition to `orchid/tools/` with no architectural change.

---

## Phase 3 — Subagent Worktree Isolation

**Items:** Subagent worktree isolation (delegate sub-tasks to isolated working directories)

**Why third:**
Worktrees are a git feature, so Phase 2 must land first. Once git is first-class, the `delegate` tool can spin a `git worktree add` for each sub-task, run the agent there, and merge or discard on completion. This gives delegate the same isolation guarantee that Claude Code's worktree model provides.

This is also the prerequisite for safe parallel execution — you cannot run two agents on the same working tree without conflict.

---

## Phase 4 — Parallelism / Concurrent Agent Execution

**Items:** Parallelism / concurrent agent execution (async task dispatch with dependency graph awareness)

**Why fourth:**
Parallelism requires isolated environments (Phase 3) and capability scoping (Phase 1) before it is safe. The dependency graph already exists in `tasks.md` (`needs:` annotations) and is parsed by the orchestrator; this phase wires it to an async dispatcher that fans out tasks with no unresolved dependencies rather than running them serially.

This is the biggest architectural change: the orchestrator's inner loop becomes async, the Claude semaphore (D0022) gets extended to a per-provider semaphore pool, and the runner must aggregate results from concurrent tasks. Do it here, after the simpler phases are stable, so there is a solid test baseline to catch regressions.

---

## Phase 5 — Dynamic Agent Spawning

**Items:** Dynamic agent spawning mid-task (agents spawn subagents based on what a task actually needs)

**Why fifth:**
Dynamic spawning is parallelism with runtime discovery of the work structure rather than static pre-definition. It requires the async dispatcher (Phase 4), worktree isolation (Phase 3 — each spawned agent needs its own tree), and the permissions model (Phase 1 — spawned agents must inherit or narrow their parent's capability set, never expand it).

Landing this before parallelism is stable would make the agent loop much harder to reason about and debug.

---

## Phase 6 — Cross-Project Agent Sharing

**Items:** Cross-project agent sharing (pool specialized agents serving multiple projects)

**Why sixth:**
A shared agent pool only makes sense once individual agents are parallel-capable (Phase 4) and dynamically spawnable (Phase 5). The pool is essentially a scheduler over the same async dispatch layer, extended to route tasks across project boundaries. The capability/permissions model (Phase 1) is also critical here — a pooled TesterAgent must not leak context or write access from one project into another.

Operationally, this requires the `AgentManager` (D0035) to become a multi-project singleton, likely surfaced through `orchid serve`.

---

## Phase 7 — Formal Resource / Cost Scheduling

**Items:** Formal resource / cost scheduling (cost-aware scheduler with token budget, rate limit pressure, latency targets)

**Why last:**
Cost scheduling is a meta-layer over everything else: it needs real operational data (audit logs from Phase 1), a parallel dispatcher to route tasks (Phase 4), and potentially a shared agent pool to shift load to (Phase 6). Building it before those layers exist means scheduling against guesses rather than real pressure signals.

The existing per-agent provider override system (D0030) and provider check (D0032) are the hooks this scheduler will extend. The implementation is additive — inject a cost-aware routing step between task selection and agent dispatch.

---

## Summary Table

| Phase | Theme | Depends On | Risk |
|-------|-------|------------|------|
| 1 | Security Hardening | — | Low |
| 2 | Native Git | Phase 1 | Low |
| 3 | Worktree Isolation | Phase 2 | Medium |
| 4 | Parallelism | Phase 1, 3 | High |
| 5 | Dynamic Spawning | Phase 4 | High |
| 6 | Cross-Project Pool | Phase 4, 5 | High |
| 7 | Cost Scheduling | Phase 1, 4, 6 | Medium |
