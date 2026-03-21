"""Gate system — human-in-the-loop control for lifecycle phase transitions.

Gate types:
  "human"  — waits for explicit `orchid --approve` before advancing
  "auto"   — opens automatically once prerequisites are met

Config resolution (highest priority first):
  1. state.gates[transition_key].type  (already approved / set via --approve)
  2. project .orchid.yaml  lifecycle.gates.<transition_key>
  3. defaults.yaml  gates.<transition_key>
  4. defaults.yaml  gates.default  (fallback, default "human")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum

from orchid import config as cfg
from orchid.lifecycle import ProjectLifecycle

logger = logging.getLogger(__name__)


class GateStatus(str, Enum):
    OPEN = "OPEN"        # can advance immediately
    WAITING = "WAITING"  # human gate, awaiting approval
    BLOCKED = "BLOCKED"  # prerequisites not met


class GateSystem:
    def __init__(self, lifecycle: ProjectLifecycle) -> None:
        self.lifecycle = lifecycle

    # ── Public API ────────────────────────────────────────────────────────────

    def check_gate(self, to_phase: str) -> GateStatus:
        """Return the current gate status for an intended transition."""
        if not self._prerequisites_met(to_phase):
            return GateStatus.BLOCKED

        from_phase = self.lifecycle.current_phase()
        key = ProjectLifecycle._transition_key(from_phase, to_phase)
        gate_type = self._resolve_gate_type(key)

        if gate_type == "auto":
            return GateStatus.OPEN

        # Human gate — check for stored approval
        gate_state = self.lifecycle.state.gates.get(key, {})
        if gate_state.get("approved"):
            return GateStatus.OPEN

        return GateStatus.WAITING

    def approve(self, to_phase: str, approver: str = "human") -> None:
        """Record approval for the current → to_phase transition."""
        from_phase = self.lifecycle.current_phase()
        key = ProjectLifecycle._transition_key(from_phase, to_phase)
        self.lifecycle.state.gates[key] = {
            "type": "human",
            "approved": True,
            "approver": approver,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        self.lifecycle.save()
        logger.info("Gate approved: %s → %s by %s", from_phase, to_phase, approver)

    def notify_gate_reached(self, to_phase: str) -> None:
        """Log (and optionally notify) that a human gate has been reached."""
        from_phase = self.lifecycle.current_phase()
        project_name = self.lifecycle.state.project_name
        msg = (
            f"\u23f8 [{project_name}] ready to advance from {from_phase} \u2192 {to_phase}\n"
            f"Review artifacts then: orchid --project . --approve"
        )
        logger.info("Gate reached: %s", msg)
        # TODO: forward to Telegram/Slack via notification system (D0020)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve_gate_type(self, transition_key: str) -> str:
        """5-layer gate type resolution."""
        # 1. State-level override (set by approve() or direct state edit)
        stored = self.lifecycle.state.gates.get(transition_key, {})
        if stored.get("type"):
            return stored["type"]

        # 2. Project .orchid.yaml lifecycle.gates
        proj_gates: dict = cfg.get("lifecycle.gates", {})
        if transition_key in proj_gates:
            return proj_gates[transition_key]

        # 3. defaults.yaml gates.<key>
        val = cfg.get(f"gates.{transition_key}")
        if val:
            return val

        # 4. Global default
        return cfg.get("gates.default", "human")

    def _prerequisites_met(self, to_phase: str) -> bool:
        """Check artifacts required before entering to_phase."""
        prereqs: dict[str, list[str]] = {
            "REQUIREMENTS": [],
            "PLANNING":     ["REQUIREMENTS.md"],
            "READY":        ["REQUIREMENTS.md", "ARCHITECTURE.md", "MILESTONES.md"],
            "EXECUTING":    ["tasks.md"],
        }
        required = prereqs.get(to_phase, [])
        return all((self.lifecycle.project_dir / a).exists() for a in required)
