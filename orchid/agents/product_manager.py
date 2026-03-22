"""ProductManagerAgent — generates REQUIREMENTS.md and ARCHITECTURE.md.

Reads: discussion history, context.md, machine profile.
Writes: <project>/REQUIREMENTS.md and <project>/ARCHITECTURE.md.
Provider: configurable (default: claude).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_REQUIREMENTS_PROMPT = """\
You are a senior product manager. Based on the discussion history and context
below, write a comprehensive REQUIREMENTS.md for the project.

## Discussion History
{discussion_history}

## Captured Context
{context_md}

## Developer Environment
{machine_context}

Write REQUIREMENTS.md in this EXACT format:

# Requirements — {project_name}

## Functional Requirements
FR-001: [testable requirement, starting with a verb]
FR-002: ...
(continue for all requirements discovered in discussion)

## Non-Functional Requirements
NFR-001: Performance — [specific metric if known, else reasonable default]
NFR-002: Security — [auth, data protection, etc.]
NFR-003: Reliability — [uptime, error handling]

## Out of Scope
- [item explicitly excluded or not discussed]

## Open Questions
- [unresolved questions that need answers before or during development]

Be specific and testable. Each requirement should describe observable behaviour.
Respond with ONLY the content of REQUIREMENTS.md — no preamble, no explanation.
"""

_ARCHITECTURE_PROMPT = """\
You are a senior software architect. Based on the requirements and discussion
below, write a comprehensive ARCHITECTURE.md for the project.

## Requirements
{requirements}

## Discussion History (summary)
{context_md}

## Developer Environment
{machine_context}

Write ARCHITECTURE.md in this EXACT format:

# Architecture — {project_name}

## Tech Stack
- Backend: [framework] ([language version])
- Frontend: [framework] (if applicable)
- Database: [primary database]
- Cache: [if applicable]
- Auth: [approach]
- Testing: [frameworks]
- Deployment: [container / platform]

## System Overview
[ASCII diagram showing main components and data flow. Keep it simple — 10-20 lines.]

## Key Design Decisions
| Decision | Choice | Rationale |
|----------|--------|-----------|
| [area] | [choice] | [1-line reason] |
(5-10 rows covering the most important decisions)

## Infrastructure
- Deployment: [Docker / bare-metal / cloud]
- Reverse proxy: [traefik / nginx / caddy]
- Domain: [if known]
- CI/CD: [if applicable]

## Directory Structure
```
project-name/
  src/           # or app/, backend/, etc.
  tests/
  [other key dirs]
```

## API Design (if applicable)
[Brief description of main endpoints or leave this section out if no API]

Respond with ONLY the content of ARCHITECTURE.md — no preamble, no explanation.
"""


@dataclass
class PMResult:
    requirements_path: Path
    architecture_path: Path


class ProductManagerAgent:
    """Generates REQUIREMENTS.md and ARCHITECTURE.md from discussion history."""

    agent_type = "product_manager"

    def __init__(
        self,
        project_dir: Path,
        cli_override: str | None = None,
        offline: bool = False,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self._cli_override = cli_override
        self._offline = offline

    def run(self, machine_profile=None) -> PMResult:
        """Read discussion history and generate REQUIREMENTS.md + ARCHITECTURE.md."""
        from orchid import config as cfg
        from orchid.discussion import DiscussionHistory
        from orchid.machine_profile import MachineProfile

        cfg.configure_for_project(self.project_dir)

        if machine_profile is None:
            machine_profile = MachineProfile.load()

        history = DiscussionHistory.load(self.project_dir)
        machine_context = machine_profile.to_context_string()
        context_md = history.get_context_md()
        discussion_history = history.to_prompt_context()
        project_name = self.project_dir.name

        provider = self._get_provider()

        # ── REQUIREMENTS.md ────────────────────────────────────────────────────
        logger.info("Generating REQUIREMENTS.md for %s", project_name)
        req_prompt = _REQUIREMENTS_PROMPT.format(
            discussion_history=discussion_history or "(no discussion yet)",
            context_md=context_md,
            machine_context=machine_context,
            project_name=project_name,
        )
        requirements_content = provider.complete(
            [{"role": "user", "content": req_prompt}],
            cacheable_prefix=1,
        )
        req_path = self.project_dir / "REQUIREMENTS.md"
        req_path.write_text(requirements_content.strip() + "\n", encoding="utf-8")
        logger.info("Wrote %s", req_path)

        # ── ARCHITECTURE.md ────────────────────────────────────────────────────
        logger.info("Generating ARCHITECTURE.md for %s", project_name)
        arch_prompt = _ARCHITECTURE_PROMPT.format(
            requirements=requirements_content,
            context_md=context_md,
            machine_context=machine_context,
            project_name=project_name,
        )
        architecture_content = provider.complete(
            [{"role": "user", "content": arch_prompt}],
            cacheable_prefix=1,
        )
        arch_path = self.project_dir / "ARCHITECTURE.md"
        arch_path.write_text(architecture_content.strip() + "\n", encoding="utf-8")
        logger.info("Wrote %s", arch_path)

        return PMResult(requirements_path=req_path, architecture_path=arch_path)

    # ── Provider ──────────────────────────────────────────────────────────────

    def _get_provider(self):
        from orchid import config as cfg
        from orchid.providers.registry import get_registry

        registry = get_registry()
        if self._offline:
            registry.set_offline(True)

        warn = cfg.get("lifecycle.warn_on_local_planning", True)
        provider = registry.resolve("product_manager", cli_override=self._cli_override)
        if warn and getattr(provider, "provider_type", "") not in ("anthropic",):
            logger.warning(
                "Using local model for ProductManagerAgent — document quality may differ."
            )
        return provider
