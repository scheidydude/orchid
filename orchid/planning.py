# orchid/planning.py
import json
import os
import asyncio
import re
from pathlib import Path
from typing import Optional, Callable, Awaitable

from orchid.providers.anthropic_provider import AnthropicProvider


SYSTEM_PROMPT = """You are a senior product manager and software architect helping to plan a new software project.

Your job is to have a conversation with the user to understand their project requirements, then generate planning artifacts.

## Conversation Flow
1. Ask clarifying questions about the project (purpose, users, key features, tech stack preferences, scale)
2. Once you have enough information, summarize what you've learned and ask if they're ready to generate the planning documents
3. When the user says they're ready (e.g. "yes", "go ahead", "generate", "done"), generate the following artifacts:

## Artifacts to Generate
When ready, generate ALL of the following in sequence:

### REQUIREMENTS.md
A comprehensive requirements document with:
- Project overview and goals
- User personas
- Functional requirements (numbered list)
- Non-functional requirements
- Out of scope items

### ARCHITECTURE.md  
A technical architecture document with:
- System overview
- Component diagram (ASCII art)
- Technology stack with justifications
- Data models
- API design (if applicable)
- Deployment architecture

### tasks.md
A tasks.md file in orchid format:
- [ ] **T001** Task title `type:code_generate` `p1`
- [ ] **T002** Another task `type:code_generate` `p1` `needs:T001`

(Use appropriate task types: code_generate, draft, review, rollup)

## Output Format for Artifacts
When generating artifacts, use this EXACT format:

<artifact name="REQUIREMENTS.md">
[content here]
</artifact>

<artifact name="ARCHITECTURE.md">
[content here]
</artifact>

<artifact name="tasks.md">
[content here]
</artifact>

Always generate all three artifacts when the user is ready. Be thorough and specific based on the conversation."""


class PlanningSession:
    def __init__(self, project_path: str):
        self.project_path = project_path
        self.history_file = Path(project_path) / '.orchid' / 'planning_history.json'
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.provider = AnthropicProvider()
        self.conversation = []
        self._load_history()

    def _load_history(self):
        if self.history_file.exists():
            try:
                data = json.loads(self.history_file.read_text())
                self.conversation = data.get('conversation', [])
            except Exception:
                self.conversation = []

    def _save_history(self):
        data = {'conversation': self.conversation}
        self.history_file.write_text(json.dumps(data, indent=2))

    def get_history(self):
        """Return conversation history for display."""
        return [
            {'role': msg['role'], 'content': msg['content']}
            for msg in self.conversation
        ]

    async def chat(
        self,
        user_message: str,
        status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        """Send a message and get a response.

        Args:
            user_message: The user's message.
            status_callback: An async callable that receives status strings to
                stream progress back to the client (e.g. over a WebSocket).
        """
        self.conversation.append({'role': 'user', 'content': user_message})

        messages = [{'role': m['role'], 'content': m['content']} for m in self.conversation]

        response = await asyncio.to_thread(
            self.provider.complete,
            messages=messages,
            system=SYSTEM_PROMPT,
            max_tokens=4096,
        )

        self.conversation.append({'role': 'assistant', 'content': response})
        self._save_history()

        # Check if response contains artifacts and save them
        if '<artifact' in response:
            await self._save_artifacts(response, status_callback=status_callback)

        return response

    async def _save_artifacts(
        self,
        response: str,
        status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        """Parse and save artifact files from the response."""
        pattern = r'<artifact name="([^"]+)">(.*?)</artifact>'
        matches = re.findall(pattern, response, re.DOTALL)
        saved = []
        for filename, content in matches:
            if status_callback:
                await status_callback(f'Generating {filename}...')
            filepath = Path(self.project_path) / filename
            filepath.write_text(content.strip())
            saved.append(filename)
        if status_callback and saved:
            await status_callback(f'artifacts_ready:{",".join(saved)}')