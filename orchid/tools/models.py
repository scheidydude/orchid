"""Unified model caller — routes to Claude API or local llama.cpp."""

from __future__ import annotations

import os
from typing import Any

from orchid import config as cfg


class Message:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def _claude_client():
    import anthropic  # lazy import
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def _openai_client(base_url: str, api_key: str):
    from openai import OpenAI  # lazy import
    return OpenAI(base_url=base_url, api_key=api_key)


def call(
    messages: list[Message | dict[str, str]],
    model_key: str = "local",
    system: str | None = None,
    **kwargs: Any,
) -> str:
    """Call a model by config key ('claude' or 'local') and return text."""
    model_cfg = cfg.get(f"models.{model_key}", {})
    provider = model_cfg.get("provider", "openai_compat")
    model_name = model_cfg.get("model", "local-model")
    max_tokens = model_cfg.get("max_tokens", 2048)
    temperature = model_cfg.get("temperature", 0.5)

    # Normalise messages
    raw = [m.to_dict() if isinstance(m, Message) else m for m in messages]

    if provider == "anthropic":
        client = _claude_client()
        response = client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or "You are a helpful assistant.",
            messages=raw,
            **kwargs,
        )
        return response.content[0].text

    # openai_compat (llama.cpp or any OpenAI-compatible endpoint)
    base_url = model_cfg.get("base_url", os.environ.get("LLAMA_BASE_URL", "http://localhost:8080/v1"))
    api_key = model_cfg.get("api_key", os.environ.get("LLAMA_API_KEY", "none"))
    client = _openai_client(base_url, api_key)

    if system:
        raw = [{"role": "system", "content": system}] + raw

    response = client.chat.completions.create(
        model=model_name,
        messages=raw,
        max_tokens=max_tokens,
        temperature=temperature,
        **kwargs,
    )
    return response.choices[0].message.content or ""


def route(task_type: str) -> str:
    """Return the config model key for a given task type."""
    claude_tasks = set(cfg.get("routing.claude_tasks", []))
    local_tasks = set(cfg.get("routing.local_tasks", []))
    if task_type in claude_tasks:
        return "claude"
    if task_type in local_tasks:
        return "local"
    return cfg.get("routing.default", "local")
