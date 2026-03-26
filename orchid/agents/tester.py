"""TesterAgent — verification-only agent for running tests and checking syntax."""

from __future__ import annotations

from orchid.agents.base import BaseAgent
from orchid.tools.shell import detect_python_runner


class TesterAgent(BaseAgent):
    """QA verification agent. Runs tests and reports results — does not write code."""

    model_key = "local"
    agent_type = "tester"
    agent_name = "tester"

    def system_prompt(self) -> str:
        env = self.environment
        runner_hint = self._test_runner_hint(env)
        base = super().system_prompt()
        return (
            "You are a QA verification agent. "
            "Your ONLY job is to run tests and report results. "
            "Do NOT write or modify code.\n\n"
            "## Verification Workflow\n"
            "1. Detect the test runner for this project (see environment below)\n"
            "2. Run the appropriate test command\n"
            "3. Parse and report results in this exact format:\n\n"
            "Final Answer: {"
            '"passed": true/false, '
            '"tests_run": <number>, '
            '"failures": ["<description>", ...], '
            '"files_checked": ["<path>", ...]'
            "}\n\n"
            f"## Test Runner\n{runner_hint}\n\n"
        ) + base

    def _test_runner_hint(self, env: str) -> str:
        if not self.project_dir:
            return "Use: python3 -m pytest"
        runner = detect_python_runner(self.project_dir)
        hints = {
            "docker": (
                "docker compose exec <service> python -m pytest -v\n"
                "Or: docker compose run --rm <service> python -m pytest -v"
            ),
            "venv": f"{runner} -m pytest -v",
            "node": "npm test  or  npx jest --verbose",
            "python": "python3 -m pytest -v",
        }
        return hints.get(env, "python3 -m pytest -v  or  npm test")
