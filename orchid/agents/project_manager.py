"""ProjectManagerAgent — generates MILESTONES.md and tasks.md.

Reads: REQUIREMENTS.md, ARCHITECTURE.md, machine profile.
Writes: <project>/MILESTONES.md and <project>/tasks.md.
Provider: configurable (default: claude).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_MILESTONES_PROMPT = """\
You are a senior project manager. Based on the requirements and architecture
below, design a milestone plan for the project.

## Requirements
{requirements}

## Architecture
{architecture}

## Developer Environment
{machine_context}

Write MILESTONES.md in this EXACT format:

# Milestones — {project_name}

## Milestone 1: Foundation
**Goal:** [what this milestone delivers — one sentence]
**Tasks:** ~[N] tasks
**Depends on:** none

[Brief list of what's included — infrastructure, core data models, auth skeleton, etc.]

## Milestone 2: Core Features
**Goal:** ...
**Tasks:** ~[N] tasks
**Depends on:** Milestone 1

[Brief list of what's included]

(Add as many milestones as the project warrants — typically 3-6)

## Milestone N: Polish & Deploy
**Goal:** Production-ready deployment
**Tasks:** ~[N] tasks
**Depends on:** Milestone N-1

[Final testing, docs, deployment config]

Keep milestones 1-3 weeks of solo developer work each.
Respond with ONLY the content of MILESTONES.md — no preamble.
"""

_TASKS_PROMPT = """\
You are a senior project manager and tech lead. Generate a complete tasks.md
for the project based on the requirements, architecture, and milestones below.

## Requirements
{requirements}

## Architecture
{architecture}

## Milestones
{milestones}

## Developer Environment
{machine_context}

## Tasks.md Format Rules
Each task line MUST follow this exact format:
  - [ ] **T001** Title `type:TYPE` `pN` [`needs:T001,T002`] [`model:MODEL`]

Where:
- T001, T002... are sequential IDs
- TYPE is one of: code_generate, review, orchestrate, plan, summarize, draft, rollup
- pN is priority: p1 (high), p2 (medium), p3 (low)
- needs: is optional, lists prerequisite task IDs
- model: is optional — use model:claude for complex tasks, model:local for simple ones

## Model Assignment Rules
Use model:claude for:
  - Authentication, authorization, JWT, OAuth
  - Complex algorithms, parsers, state machines
  - Database schema design, migrations
  - API design and complex business logic
  - Security-sensitive code
  - Review tasks (always claude)

Use model:local for:
  - Boilerplate setup (Dockerfile, .gitignore, README)
  - Simple CRUD endpoints
  - Configuration files
  - Basic HTML/CSS templates
  - Simple data transformations

## Task Scoping Rules
- Each task should touch at most 2-3 files
- Group related work, don't over-split simple things
- After every 5-8 code tasks, add a review task
- Add a rollup task at the end of each milestone:
  - [ ] **T0NN** Milestone N summary `type:rollup` `p2` `rollup:T001,T002,...` `output:MILESTONE-N.md`
- Final task should be a comprehensive rollup across all milestones

## Example tasks.md snippet
```
# Tasks — myapp

- [ ] **T001** Scaffold FastAPI project with poetry/uv `type:code_generate` `p1` `model:local`
- [ ] **T002** Design database schema for users and posts `type:code_generate` `p1` `model:claude`
- [ ] **T003** Implement User model and Alembic migrations `type:code_generate` `p1` `needs:T002` `model:claude`
- [ ] **T004** Implement JWT authentication endpoints `type:code_generate` `p1` `needs:T001,T002` `model:claude`
- [ ] **T005** Review auth implementation `type:review` `p1` `needs:T004`
- [ ] **T006** Milestone 1 rollup `type:rollup` `p2` `rollup:T001,T002,T003,T004,T005` `output:MILESTONE-1.md`
```

Now generate a complete tasks.md for {project_name}. Include all tasks needed
to fully implement the project across all milestones.

Respond with ONLY the content of tasks.md — start with `# Tasks — {project_name}`.
"""


@dataclass
class PMgrResult:
    milestones_path: Path
    tasks_path: Path
    task_count: int


class ProjectManagerAgent:
    """Generates MILESTONES.md and tasks.md from requirements and architecture."""

    agent_type = "project_manager"

    def __init__(
        self,
        project_dir: Path,
        cli_override: str | None = None,
        offline: bool = False,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self._cli_override = cli_override
        self._offline = offline

    def run(self, machine_profile=None) -> PMgrResult:
        """Generate MILESTONES.md and tasks.md from existing project artifacts."""
        from orchid import config as cfg
        from orchid.machine_profile import MachineProfile

        cfg.configure_for_project(self.project_dir)

        if machine_profile is None:
            machine_profile = MachineProfile.load()

        req_path = self.project_dir / "REQUIREMENTS.md"
        arch_path = self.project_dir / "ARCHITECTURE.md"

        requirements = req_path.read_text(encoding="utf-8") if req_path.exists() else ""
        architecture = arch_path.read_text(encoding="utf-8") if arch_path.exists() else ""
        machine_context = machine_profile.to_context_string()
        project_name = self.project_dir.name

        provider = self._get_provider()

        # ── MILESTONES.md ──────────────────────────────────────────────────────
        logger.info("Generating MILESTONES.md for %s", project_name)
        mil_prompt = _MILESTONES_PROMPT.format(
            requirements=requirements or "(no requirements yet)",
            architecture=architecture or "(no architecture yet)",
            machine_context=machine_context,
            project_name=project_name,
        )
        milestones_content = provider.complete(
            [{"role": "user", "content": mil_prompt}], cacheable_prefix=1
        )
        mil_path = self.project_dir / "MILESTONES.md"
        mil_path.write_text(milestones_content.strip() + "\n", encoding="utf-8")
        logger.info("Wrote %s", mil_path)

        # ── tasks.md ───────────────────────────────────────────────────────────
        logger.info("Generating tasks.md for %s", project_name)
        tasks_prompt = _TASKS_PROMPT.format(
            requirements=requirements or "(no requirements yet)",
            architecture=architecture or "(no architecture yet)",
            milestones=milestones_content,
            machine_context=machine_context,
            project_name=project_name,
        )
        tasks_content = provider.complete(
            [{"role": "user", "content": tasks_prompt}], cacheable_prefix=1
        )
        tasks_path = self.project_dir / "tasks.md"
        tasks_path.write_text(tasks_content.strip() + "\n", encoding="utf-8")
        logger.info("Wrote %s", tasks_path)

        task_count = tasks_content.count("- [ ]")

        return PMgrResult(
            milestones_path=mil_path,
            tasks_path=tasks_path,
            task_count=task_count,
        )

    # ── Provider ──────────────────────────────────────────────────────────────

    def _get_provider(self):
        from orchid import config as cfg
        from orchid.providers.registry import get_registry

        registry = get_registry()
        if self._offline:
            registry.set_offline(True)

        warn = cfg.get("lifecycle.warn_on_local_planning", True)
        provider = registry.resolve("project_manager", cli_override=self._cli_override)
        if warn and getattr(provider, "provider_type", "") not in ("anthropic",):
            logger.warning(
                "Using local model for ProjectManagerAgent — task quality may differ."
            )
        return provider
