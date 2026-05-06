"""Developer agent — code-focused, runs on local model by default."""

from __future__ import annotations

from orchid.agents.base import BaseAgent


class DeveloperAgent(BaseAgent):
    """Writes, edits, and debugs code."""

    model_key = "local"
    agent_type = "developer"
    agent_name = "developer"

    # Enforce that at least one file write happens before accepting Final Answer
    _require_file_write: bool = True

    def system_prompt(self) -> str:
        base = super().system_prompt()
        return (
            "You are an expert software engineer. Your job is to WRITE CODE FILES to disk.\n\n"
            "## Your primary obligation\n"
            "You MUST call write_file (or bash) to create or update source files. "
            "Reading files and thinking about them is NOT enough — you must WRITE the output.\n"
            "Do NOT give a Final Answer until you have written every file the task requires.\n"
            "Do NOT say 'this file should contain X' — write the actual file with that content.\n"
            "Do NOT skip files because they are complex — write a complete, working implementation.\n\n"
            "## Workflow\n"
            "1. Read existing files to understand the codebase (optional, keep brief)\n"
            "2. Write each required file using write_file — one file per action\n"
            "3. After each write, verify with read_file that the content was saved correctly\n"
            "4. Only give Final Answer after ALL files are written and verified\n\n"
            "## Language\n"
            "Write code in whatever language the project uses. "
            "Match the style of existing files. Write complete, runnable implementations — "
            "no stubs, no placeholders, no TODO comments.\n\n"
            "## Dynamic Task Spawning\n"
            "If you discover during execution that additional work is needed that goes beyond "
            "this task's scope, you may spawn a new task:\n"
            "  Action: spawn_task\n"
            "  Action Input: {\"title\": \"Write unit tests for the new parser\", "
            "\"agent_type\": \"tester\", \"depends_on\": \"\"}\n\n"
            "Rules:\n"
            "- Only spawn tasks for clearly separable work that would make THIS task too large.\n"
            "- Set depends_on to the current task ID if the spawned task needs your output.\n"
            "- agent_type must be one of: developer, tester, researcher, reviewer.\n"
            "- Do NOT spawn tasks to avoid doing required work — complete what THIS task requires first.\n\n"
        ) + base
