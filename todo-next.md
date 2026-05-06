**Subagent worktree isolation**
- delegate sub-tasks to isolated working directories to match Claude Code's worktree model.

**Native git integration**
— commit, branch, PR operations as first-class tools rather than bash passthrough.

**Circuit breakers for HTTP hooks**
— suggested in T100 security review; implement for production hardening.

**Audit logging for shell hooks**
— log command, exit code, output to `.orchid/hook_audit.jsonl`.

**Parallelism / concurrent agent execution**
Orchid is largely sequential — one task, one agent at a time per project. A mature Agentic OS would fan out independent tasks to parallel agents. You'd need async task dispatch and dependency graph awareness to block only when there's a true dependency.

**Dynamic agent spawning mid-task**
Agents are typed and pre-defined. True Agentic OSes (like Claude Code Agent Teams) can spawn subagents dynamically during execution based on what a task actually needs.

**Cross-project agent sharing**
Each project runs its own agent instances. A more OS-like approach would pool specialized agents (e.g., one TesterAgent serving multiple projects, scheduled by demand).

**Formal resource / cost scheduling**
You have per-agent provider overrides (great), but no cost-aware scheduler that dynamically routes based on token budget, rate limit pressure, or latency targets.

**Sandboxing / permissions model**
Tools aren't capability-scoped per agent. A kernel-like OS would restrict which tools each agent class can call (TesterAgent can't write files, DiscussionAgent can't execute code, etc.).
