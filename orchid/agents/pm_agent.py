"""PM Agent — product manager discussion and artifact generation."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# Type alias for async status callback: (event_type, message) -> None
StatusCallback = Callable[[str, str], Awaitable[None]]


class PMAgent:
    """Conversational PM agent that gathers requirements and generates artifacts."""

    def __init__(self, project_path: str):
        self.project_path = project_path
        self._history: list[dict] = []
        self._artifacts_generated = False

    def get_history(self) -> list[dict]:
        return self._history

    def add_to_history(self, role: str, content: str) -> None:
        self._history.append({"role": role, "content": content})

    async def chat(self, user_message: str) -> str:
        """Have a conversation with the user about their project."""
        try:
            from orchid.providers import get_provider
            provider = get_provider("claude")

            system_prompt = (
                "You are an expert Product Manager AI assistant. Your role is to:\n"
                "1. Understand the user's project requirements through conversation\n"
                "2. Ask clarifying questions about features, target users, technical constraints\n"
                "3. Help define the scope and architecture\n"
                "4. When the user says they're done or ready, generate comprehensive artifacts\n\n"
                "Keep responses concise and focused. Ask one or two questions at a time."
            )

            messages = [{"role": m["role"], "content": m["content"]} for m in self._history]
            messages.append({"role": "user", "content": user_message})

            response = await provider.complete(
                prompt=user_message,
                system=system_prompt,
                messages=messages[:-1],  # history without current message
            )
            return response
        except Exception as e:
            logger.error("PM chat error: %s", e)
            return f"I encountered an error: {e}. Please try again."

    async def generate_artifacts(
        self,
        status_callback: StatusCallback | None = None,
    ) -> str:
        """Generate project artifacts based on the conversation.

        Args:
            status_callback: Optional async callable ``(event_type, message)``
                that receives progress updates.  ``event_type`` is one of:

                * ``'status'``   – human-readable progress message
                * ``'progress'`` – integer percentage as a string, e.g. ``"33"``
                * ``'error'``    – error description
        """

        async def _emit(event: str, msg: str) -> None:
            if status_callback:
                try:
                    await status_callback(event, msg)
                except Exception:
                    pass

        try:
            from orchid.providers import get_provider
            provider = get_provider("claude")

            conversation_summary = "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in self._history
            )

            # ── REQUIREMENTS.md ───────────────────────────────────────────────
            await _emit("status", "Generating REQUIREMENTS.md…")
            await _emit("progress", "10")

            req_prompt = (
                "Based on this conversation, generate a comprehensive REQUIREMENTS.md file:\n\n"
                f"{conversation_summary}\n\n"
                "Create a detailed requirements document with:\n"
                "- Project Overview\n"
                "- Functional Requirements\n"
                "- Non-Functional Requirements\n"
                "- User Stories\n"
                "- Acceptance Criteria"
            )
            requirements = await provider.complete(req_prompt)
            (Path(self.project_path) / "REQUIREMENTS.md").write_text(requirements)
            await _emit("status", "REQUIREMENTS.md written")
            await _emit("progress", "40")

            # ── ARCHITECTURE.md ───────────────────────────────────────────────
            await _emit("status", "Generating ARCHITECTURE.md…")
            await _emit("progress", "45")

            arch_prompt = (
                "Based on this conversation, generate a comprehensive ARCHITECTURE.md file:\n\n"
                f"{conversation_summary}\n\n"
                "Create a detailed architecture document with:\n"
                "- System Overview\n"
                "- Technology Stack\n"
                "- Component Architecture\n"
                "- Data Flow\n"
                "- API Design\n"
                "- Deployment Architecture"
            )
            architecture = await provider.complete(arch_prompt)
            (Path(self.project_path) / "ARCHITECTURE.md").write_text(architecture)
            await _emit("status", "ARCHITECTURE.md written")
            await _emit("progress", "75")

            # ── tasks.md ──────────────────────────────────────────────────────
            await _emit("status", "Generating tasks.md…")
            await _emit("progress", "80")

            tasks_prompt = (
                "Based on this conversation, generate a tasks.md file for the orchid task system:\n\n"
                f"{conversation_summary}\n\n"
                "Create tasks in this exact format:\n"
                "- [ ] **T001** Task title `type:code_generate` `p1`\n"
                "- [ ] **T002** Another task `type:code_generate` `p1` `needs:T001`\n\n"
                "Generate 10-15 concrete implementation tasks."
            )
            tasks_content = await provider.complete(tasks_prompt)
            (Path(self.project_path) / "tasks.md").write_text(tasks_content)
            await _emit("status", "tasks.md written")
            await _emit("progress", "100")

            self._artifacts_generated = True

            return (
                "I have generated your project artifacts:\n"
                "- REQUIREMENTS.md\n"
                "- ARCHITECTURE.md\n"
                "- tasks.md\n\n"
                "You can now run the orchid agent to start implementing your project!"
            )

        except Exception as e:
            logger.error("Artifact generation error: %s", e)
            await _emit("error", str(e))
            return f"Error generating artifacts: {e}"