"""Project lifecycle state machine for Orchid V2.

Persisted at <project>/.orchid/project.state.json

T097: Hooks are fired on phase transitions via the HookRegistry.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PHASES = ["NEW", "DISCUSSING", "REQUIREMENTS", "PLANNING", "READY", "EXECUTING", "COMPLETE"]

# Valid forward transitions (from_phase → set of allowed to_phases)
# Any phase can also return to DISCUSSING.
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "NEW":          {"DISCUSSING"},
    "DISCUSSING":   {"REQUIREMENTS"},
    "REQUIREMENTS": {"PLANNING"},
    "PLANNING":     {"READY"},
    "READY":        {"EXECUTING"},
    "EXECUTING":    {"COMPLETE", "PLANNING"},
    "COMPLETE":     {"PLANNING"},
}

# Inject the universal DISCUSSING escape hatch
for _p in PHASES:
    _VALID_TRANSITIONS.setdefault(_p, set()).add("DISCUSSING")


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ProjectState:
    phase: str = "NEW"
    project_name: str = ""
    created_at: str = field(default_factory=_now)
    last_activity: str = field(default_factory=_now)
    current_milestone: str | None = None
    gates: dict = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)
    slack_channel: str | None = None
    discussion_turns: int = 0


class ProjectLifecycle:
    """Manages lifecycle phase transitions and state persistence.

    T097: Hooks are fired at the following points:
    - phase_transition: When a phase change is initiated
    - phase_enter: After successfully entering a new phase
    - phase_exit: After leaving a phase
    """

    def __init__(
        self,
        project_dir: Path,
        state: ProjectState,
        hook_registry: object | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.state = state
        self._state_path = self.project_dir / ".orchid" / "project.state.json"
        self._hook_registry = hook_registry

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        project_dir: Path,
        hook_registry: object | None = None,
    ) -> ProjectLifecycle:
        """Load from disk, or create NEW state if file absent."""
        project_dir = Path(project_dir).resolve()
        state_path = project_dir / ".orchid" / "project.state.json"

        if state_path.exists():
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read project.state.json: %s — using NEW state", exc)
                data = {}
            state = ProjectState(
                phase=data.get("phase", "NEW"),
                project_name=data.get("project_name", project_dir.name),
                created_at=data.get("created_at", _now()),
                last_activity=data.get("last_activity", _now()),
                current_milestone=data.get("current_milestone"),
                gates=data.get("gates", {}),
                artifacts=data.get("artifacts", {}),
                slack_channel=data.get("slack_channel"),
                discussion_turns=data.get("discussion_turns", 0),
            )
        else:
            state = ProjectState(
                phase="NEW",
                project_name=project_dir.name,
            )

        return cls(project_dir=project_dir, state=state, hook_registry=hook_registry)

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state.last_activity = _now()
        data = {
            "phase": self.state.phase,
            "project_name": self.state.project_name,
            "created_at": self.state.created_at,
            "last_activity": self.state.last_activity,
            "current_milestone": self.state.current_milestone,
            "gates": self.state.gates,
            "artifacts": self.state.artifacts,
            "slack_channel": self.state.slack_channel,
            "discussion_turns": self.state.discussion_turns,
        }
        self._state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── Transitions ───────────────────────────────────────────────────────────

    def current_phase(self) -> str:
        return self.state.phase

    def advance(self, phase: str) -> None:
        """Advance to *phase*; raises ValueError if transition is not valid.

        T097: Fires phase transition hooks before and after the transition.
        """
        current = self.state.phase
        allowed = _VALID_TRANSITIONS.get(current, set())
        if phase not in allowed:
            raise ValueError(
                f"Invalid transition {current!r} → {phase!r}. "
                f"Allowed: {', '.join(sorted(allowed))}"
            )

        # T097: Fire phase_transition hook before the change
        self._fire_phase_transition_hook(current, phase)

        logger.info("Lifecycle: %s → %s", current, phase)
        self.state.phase = phase
        self.save()

        # T097: Fire phase_enter hook after the change
        self._fire_phase_enter_hook(phase)

    def can_advance(self) -> bool:
        """Return True when at least one valid next phase exists."""
        return bool(_VALID_TRANSITIONS.get(self.state.phase))

    def valid_next_phases(self) -> list[str]:
        transitions = _VALID_TRANSITIONS.get(self.state.phase, set())
        return sorted(t for t in transitions if t != self.state.phase)

    # ── Artifact checks ───────────────────────────────────────────────────────

    def artifacts_complete(self) -> bool:
        """Check whether required artifacts for the current phase exist on disk."""
        phase_artifacts: dict[str, list[str]] = {
            "REQUIREMENTS": ["REQUIREMENTS.md"],
            "PLANNING":     ["REQUIREMENTS.md", "ARCHITECTURE.md"],
            "READY":        ["REQUIREMENTS.md", "ARCHITECTURE.md", "MILESTONES.md", "tasks.md"],
            "EXECUTING":    ["tasks.md"],
        }
        required = phase_artifacts.get(self.state.phase, [])
        return all((self.project_dir / a).exists() for a in required)

    # ── Gate helpers ──────────────────────────────────────────────────────────

    def gate_requires_approval(self, to_phase: str) -> bool:
        """True when a human gate is pending for this transition."""
        key = self._transition_key(self.state.phase, to_phase)
        return self.state.gates.get(key, {}).get("type", "human") == "human"

    # ── Hook firing (T097) ────────────────────────────────────────────────────

    def _fire_phase_transition_hook(self, from_phase: str, to_phase: str) -> None:
        """Fire the phase_transition hook event."""
        if self._hook_registry is None:
            return

        try:
            from orchid.hooks.events import HookEvent, PHASE_TRANSITION
            event = HookEvent(
                event_type=PHASE_TRANSITION,
                data={
                    "from_phase": from_phase,
                    "to_phase": to_phase,
                    "project_name": self.state.project_name,
                },
                context={"phase": to_phase},
            )
            self._hook_registry.fire(event)
        except Exception as e:
            logger.warning("Failed to fire phase_transition hook: %s", e)

    def _fire_phase_enter_hook(self, phase: str) -> None:
        """Fire the phase_enter hook event."""
        if self._hook_registry is None:
            return

        try:
            from orchid.hooks.events import HookEvent, PHASE_ENTER
            event = HookEvent(
                event_type=PHASE_ENTER,
                data={
                    "phase": phase,
                    "project_name": self.state.project_name,
                },
                context={"phase": phase},
            )
            self._hook_registry.fire(event)
        except Exception as e:
            logger.warning("Failed to fire phase_enter hook: %s", e)

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _transition_key(from_phase: str, to_phase: str) -> str:
        return f"{from_phase.lower()}_to_{to_phase.lower()}"