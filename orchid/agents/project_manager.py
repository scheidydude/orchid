"""ProjectManagerAgent — generates MILESTONES.md and tasks.md.

Reads: REQUIREMENTS.md, ARCHITECTURE.md, machine profile.
Writes: <project>/MILESTONES.md and <project>/tasks.md.
Provider: configurable via registry (see providers.agent_defaults.project_manager in orchid.defaults.yaml).
"""

from __future__ import annotations

import logging
import re
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

_TASKS_PROMPT_MILESTONE = """\
You are a senior project manager and tech lead. Generate the tasks.md entries
for ONE milestone only: "{milestone_name}".

## Project: {project_name}

## Requirements
{requirements}

## Architecture
{architecture}

## This Milestone
{milestone_text}

## All Milestones (for context)
{milestones}

## Developer Environment
{machine_context}

## Previously Generated Task IDs
The following task IDs have already been assigned (use these for `needs:` references
and continue numbering from the next available ID):
{prior_task_ids}

## Tasks.md Format Rules
Each task line MUST follow this exact format:
  - [ ] **T001** Title `type:TYPE` `pN` [`needs:T001,T002`] [`model:MODEL`]

Where:
- T001, T002... are sequential IDs continuing from the prior task IDs above
- TYPE is one of: code_generate, review, orchestrate, plan, summarize, draft, rollup
- pN is priority: p1 (high), p2 (medium), p3 (low)
- needs: is optional, lists prerequisite task IDs
- model: is optional — use model:claude for complex tasks, model:local for simple ones

CRITICAL: Every task title MUST be descriptive and specific — at minimum 15 characters.
WRONG: `- [ ] **T012** Scaffold`
RIGHT: `- [ ] **T012** Scaffold FastAPI project with uv and project layout`
WRONG: `- [ ] **T015** Create`
RIGHT: `- [ ] **T015** Create Dockerfile and docker-compose for local dev`

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
- Add a rollup task at the end of this milestone:
  - [ ] **T0NN** {milestone_name} summary `type:rollup` `p2` `rollup:T...,T...` `output:MILESTONE-N.md`

## Example tasks.md snippet
```
- [ ] **T001** Scaffold FastAPI project with poetry/uv `type:code_generate` `p1` `model:local`
- [ ] **T002** Design database schema for users and posts `type:code_generate` `p1` `model:claude`
- [ ] **T003** Implement User model and Alembic migrations `type:code_generate` `p1` `needs:T002` `model:claude`
- [ ] **T004** Implement JWT authentication endpoints `type:code_generate` `p1` `needs:T001,T002` `model:claude`
- [ ] **T005** Review auth implementation `type:review` `p1` `needs:T004`
- [ ] **T006** Milestone 1 rollup `type:rollup` `p2` `rollup:T001,T002,T003,T004,T005` `output:MILESTONE-1.md`
```

Output ONLY the task lines for this milestone — no headers, no preamble.
Start directly with `- [ ] **T{next_id:03d}**`.
"""

_TASKS_FINAL_ROLLUP = """\
- [ ] **T{id:03d}** Final project rollup across all milestones \
`type:rollup` `p2` `rollup:{all_ids}` `output:PROJECT-COMPLETE.md`
"""

# Minimum title length to be considered valid
_MIN_TITLE_LEN = 15
# If this fraction of tasks have short titles, treat output as incomplete
_SHORT_TITLE_THRESHOLD = 0.20
_MAX_RETRIES = 2


@dataclass
class PMgrResult:
    milestones_path: Path
    tasks_path: Path
    task_count: int


def _parse_milestones(milestones_content: str) -> list[tuple[str, str]]:
    """Return list of (milestone_name, milestone_text) pairs.

    Splits on '## Milestone N:' headings.
    """
    milestones: list[tuple[str, str]] = []
    pattern = re.compile(r"^## (Milestone \d+[^$\n]*)", re.MULTILINE)
    matches = list(pattern.finditer(milestones_content))
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(milestones_content)
        text = milestones_content[start:end].strip()
        milestones.append((name, text))
    return milestones


def _parse_task_lines(content: str) -> list[str]:
    """Extract task lines (- [ ] **T...**) from content."""
    return [
        line.strip()
        for line in content.splitlines()
        if re.match(r"^-\s*\[\s*\]\s*\*\*T\d+\*\*", line.strip())
    ]


def _extract_task_id(line: str) -> str | None:
    """Return the task ID (e.g. 'T042') from a task line."""
    m = re.search(r"\*\*(T\d+)\*\*", line)
    return m.group(1) if m else None


def _extract_task_title(line: str) -> str:
    """Return just the title portion of a task line (between ID and first backtick)."""
    m = re.search(r"\*\*T\d+\*\*\s+(.+?)\s*`", line)
    if m:
        return m.group(1).strip()
    # Fallback: everything after the ID
    m2 = re.search(r"\*\*T\d+\*\*\s+(.+)", line)
    return m2.group(1).strip() if m2 else ""


def _validate_tasks(task_lines: list[str]) -> tuple[bool, list[str]]:
    """Return (is_valid, short_title_lines).

    is_valid is False if more than _SHORT_TITLE_THRESHOLD of tasks have
    titles shorter than _MIN_TITLE_LEN characters.
    """
    short: list[str] = []
    for line in task_lines:
        title = _extract_task_title(line)
        if len(title) < _MIN_TITLE_LEN:
            short.append(line)
            logger.warning(
                "Short task title (%d chars): %r", len(title), title
            )
    if not task_lines:
        return True, []
    ratio = len(short) / len(task_lines)
    is_valid = ratio <= _SHORT_TITLE_THRESHOLD
    if not is_valid:
        logger.warning(
            "%.0f%% of tasks have short titles (threshold %.0f%%) — output flagged incomplete",
            ratio * 100,
            _SHORT_TITLE_THRESHOLD * 100,
        )
    return is_valid, short


def _renumber_tasks(task_lines: list[str], start: int) -> tuple[list[str], dict[str, str]]:
    """Renumber task IDs sequentially from `start`.

    Returns (renumbered_lines, old_to_new) where old_to_new maps old IDs to new IDs.
    """
    old_to_new: dict[str, str] = {}
    counter = start
    result: list[str] = []
    for line in task_lines:
        old_id = _extract_task_id(line)
        if old_id is None:
            result.append(line)
            continue
        new_id = f"T{counter:03d}"
        old_to_new[old_id] = new_id
        counter += 1
        result.append(line.replace(f"**{old_id}**", f"**{new_id}**", 1))
    # Second pass: fix needs: references
    fixed: list[str] = []
    for line in result:
        needs_m = re.search(r"`needs:([^`]+)`", line)
        if needs_m:
            refs = needs_m.group(1).split(",")
            new_refs = [old_to_new.get(r.strip(), r.strip()) for r in refs]
            line = line.replace(needs_m.group(0), f"`needs:{','.join(new_refs)}`")
        rollup_m = re.search(r"`rollup:([^`]+)`", line)
        if rollup_m:
            refs = rollup_m.group(1).split(",")
            new_refs = [old_to_new.get(r.strip(), r.strip()) for r in refs]
            line = line.replace(rollup_m.group(0), f"`rollup:{','.join(new_refs)}`")
        fixed.append(line)
    return fixed, old_to_new


class ProjectManagerAgent:
    """Generates MILESTONES.md and tasks.md from requirements and architecture."""

    agent_type = "project_manager"
    agent_name = "project_manager"

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

        # ── tasks.md — milestone-by-milestone ─────────────────────────────────
        logger.info("Generating tasks.md for %s (milestone-by-milestone)", project_name)
        tasks_path = self.project_dir / "tasks.md"
        all_task_lines = self._generate_tasks_by_milestone(
            provider=provider,
            requirements=requirements,
            architecture=architecture,
            milestones_content=milestones_content,
            machine_context=machine_context,
            project_name=project_name,
        )

        # Append a final cross-milestone rollup
        if all_task_lines:
            all_ids = ",".join(
                tid for line in all_task_lines if (tid := _extract_task_id(line))
            )
            final_id = len(all_task_lines) + 1
            rollup_line = (
                f"- [ ] **T{final_id:03d}** Final project rollup across all milestones "
                f"`type:rollup` `p2` `rollup:{all_ids}` `output:PROJECT-COMPLETE.md`"
            )
            all_task_lines.append(rollup_line)

        tasks_content = f"# Tasks — {project_name}\n\n" + "\n".join(all_task_lines) + "\n"
        tasks_path.write_text(tasks_content, encoding="utf-8")
        logger.info("Wrote %s (%d tasks)", tasks_path, len(all_task_lines))

        return PMgrResult(
            milestones_path=mil_path,
            tasks_path=tasks_path,
            task_count=len(all_task_lines),
        )

    def _generate_tasks_by_milestone(
        self,
        *,
        provider,
        requirements: str,
        architecture: str,
        milestones_content: str,
        machine_context: str,
        project_name: str,
    ) -> list[str]:
        """Generate tasks one milestone at a time and return renumbered task lines."""
        milestones = _parse_milestones(milestones_content)
        if not milestones:
            logger.warning("No milestones parsed — falling back to single-pass generation")
            return self._generate_tasks_single_pass(
                provider=provider,
                requirements=requirements,
                architecture=architecture,
                milestones_content=milestones_content,
                machine_context=machine_context,
                project_name=project_name,
            )

        all_lines: list[str] = []
        next_id = 1

        for idx, (milestone_name, milestone_text) in enumerate(milestones, 1):
            logger.info("Generating tasks for %s (%d/%d)", milestone_name, idx, len(milestones))

            prior_ids = (
                ", ".join(_extract_task_id(ln) for ln in all_lines if _extract_task_id(ln))
                if all_lines
                else "none yet"
            )

            prompt = _TASKS_PROMPT_MILESTONE.format(
                milestone_name=milestone_name,
                project_name=project_name,
                requirements=requirements or "(no requirements yet)",
                architecture=architecture or "(no architecture yet)",
                milestone_text=milestone_text,
                milestones=milestones_content,
                machine_context=machine_context,
                prior_task_ids=prior_ids,
                next_id=next_id,
            )

            milestone_lines = self._generate_with_retry(
                provider=provider,
                prompt=prompt,
                milestone_name=milestone_name,
            )

            # Renumber so IDs are globally sequential
            renumbered, _ = _renumber_tasks(milestone_lines, start=next_id)
            all_lines.extend(renumbered)
            next_id += len(renumbered)

        return all_lines

    def _generate_with_retry(
        self,
        *,
        provider,
        prompt: str,
        milestone_name: str,
    ) -> list[str]:
        """Call provider.complete, parse tasks, validate titles; retry up to _MAX_RETRIES."""
        for attempt in range(1, _MAX_RETRIES + 2):
            raw = provider.complete(
                [{"role": "user", "content": prompt}],
                cacheable_prefix=1,
                max_tokens=16384,
            )
            task_lines = _parse_task_lines(raw)

            if not task_lines:
                logger.warning(
                    "No task lines found for %s (attempt %d) — raw snippet: %r",
                    milestone_name,
                    attempt,
                    raw[:200],
                )
            else:
                is_valid, short_lines = _validate_tasks(task_lines)
                if is_valid:
                    return task_lines
                if attempt <= _MAX_RETRIES:
                    logger.warning(
                        "Retrying %s task generation (attempt %d/%d) due to %d short titles",
                        milestone_name,
                        attempt,
                        _MAX_RETRIES,
                        len(short_lines),
                    )
                else:
                    logger.warning(
                        "Accepting %s tasks after %d attempts despite %d short titles",
                        milestone_name,
                        attempt,
                        len(short_lines),
                    )
                    return task_lines

        return []

    def _generate_tasks_single_pass(
        self,
        *,
        provider,
        requirements: str,
        architecture: str,
        milestones_content: str,
        machine_context: str,
        project_name: str,
    ) -> list[str]:
        """Fallback: generate all tasks in one call (original behaviour)."""
        _TASKS_PROMPT = """\
You are a senior project manager and tech lead. Generate a complete tasks.md
for the project based on the requirements, architecture, and milestones below.

CRITICAL: Every task title MUST be descriptive and specific — at minimum 15 characters.
WRONG: `- [ ] **T012** Scaffold`
RIGHT: `- [ ] **T012** Scaffold FastAPI project with uv and project layout`

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

Respond with ONLY the content of tasks.md — start with `# Tasks — {project_name}`.
"""
        prompt = _TASKS_PROMPT.format(
            requirements=requirements or "(no requirements yet)",
            architecture=architecture or "(no architecture yet)",
            milestones=milestones_content,
            machine_context=machine_context,
            project_name=project_name,
        )
        raw = provider.complete(
            [{"role": "user", "content": prompt}],
            cacheable_prefix=1,
            max_tokens=16384,
        )
        return _parse_task_lines(raw)

    # ── Provider ──────────────────────────────────────────────────────────────

    def _get_provider(self):
        from orchid import config as cfg
        from orchid.providers.registry import get_registry

        registry = get_registry()
        if self._offline:
            registry.set_offline(True)

        warn = cfg.get("lifecycle.warn_on_local_planning", True)
        provider = registry.resolve("project_manager", agent_name="project_manager", cli_override=self._cli_override)
        if warn and getattr(provider, "provider_type", "") not in ("anthropic",):
            logger.warning(
                "Using local model for ProjectManagerAgent — task quality may differ."
            )
        return provider
