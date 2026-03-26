"""ProviderRegistry — 5-layer provider resolution for Orchid agents."""

from __future__ import annotations

import logging
import os
from typing import Any

from orchid.providers.base import ProviderBase, ProviderUnavailableError

logger = logging.getLogger(__name__)

# ── Registry singleton ────────────────────────────────────────────────────────

_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    """Return the global registry, loading it on first call."""
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = ProviderRegistry.load()
    return _registry


def reset_registry() -> None:
    """Force reload on next get_registry() call. Useful after config changes or in tests."""
    global _registry  # noqa: PLW0603
    _registry = None


# ── Resolution defaults ───────────────────────────────────────────────────────

# Hardcoded agent-type → provider fallbacks (layer 5)
_AGENT_DEFAULTS: dict[str, str] = {
    "orchestrator":    "claude",
    "reviewer":        "claude",
    "developer":       "local",
    "researcher":      "local",
    "base":            "local",
    # V2 strategic agents
    "discussion":      "claude",
    "product_manager": "claude",
    "project_manager": "claude",
}

# Hardcoded task-type → provider fallbacks (used when no agent-type config found)
_TASK_TYPE_DEFAULTS: dict[str, str] = {
    "orchestrate": "claude",
    "review":      "claude",
    "critique":    "claude",
    "plan":        "claude",
    "synthesize":  "claude",
    "draft":       "local",
    "code_generate": "local",
    "summarize":   "local",
    "search":      "local",
    "transform":   "local",
    "research":    "local",
}


# ── Provider instantiation helpers ────────────────────────────────────────────

def _expand_env(value: str) -> str:
    """Expand ${VAR:-default} style shell substitutions."""
    import re
    def _sub(m: re.Match) -> str:
        var, _, default = m.group(1).partition(":-")
        return os.environ.get(var, default)
    return re.sub(r"\$\{([^}]+)\}", _sub, value)


def _instantiate(name: str, pcfg: dict[str, Any]) -> ProviderBase | None:
    """Create a ProviderBase from a configured block."""
    ptype = pcfg.get("type", name)

    # Expand env vars in string config values
    cfg_expanded = {
        k: _expand_env(v) if isinstance(v, str) else v
        for k, v in pcfg.items()
    }

    if ptype == "anthropic":
        from orchid.providers.anthropic import AnthropicProvider
        return AnthropicProvider(
            model=cfg_expanded.get("model", "claude-sonnet-4-6"),
            max_tokens=int(cfg_expanded.get("max_tokens", 8192)),
            temperature=float(cfg_expanded.get("temperature", 0.3)),
        )

    if ptype in ("local", "openai_compat"):
        from orchid.providers.local import LocalProvider
        p = LocalProvider(
            base_url=cfg_expanded.get("base_url"),
            api_key=cfg_expanded.get("api_key"),
            model=cfg_expanded.get("model"),
            embed_url=cfg_expanded.get("embed_url"),
            max_tokens=int(cfg_expanded.get("max_tokens", 4096)),
            temperature=float(cfg_expanded.get("temperature", 0.5)),
        )
        p.name = name  # allow aliasing (e.g. name="local" or custom name)
        return p

    if ptype == "ollama":
        from orchid.providers.ollama import OllamaProvider
        p = OllamaProvider(
            base_url=cfg_expanded.get("base_url"),
            model=cfg_expanded.get("model"),
        )
        p.name = name
        return p

    if ptype == "openai":
        from orchid.providers.openai import OpenAIProvider
        p = OpenAIProvider(
            base_url=cfg_expanded.get("base_url", "https://api.openai.com/v1"),
            model=cfg_expanded.get("model"),
            max_tokens=int(cfg_expanded.get("max_tokens", 4096)),
            temperature=float(cfg_expanded.get("temperature", 0.5)),
        )
        p.name = name
        return p

    if ptype == "openrouter":
        from orchid.providers.openai import OpenRouterProvider
        p = OpenRouterProvider(model=cfg_expanded.get("model"))
        p.name = name
        return p

    if ptype == "bedrock":
        from orchid.providers.bedrock import BedrockProvider
        p = BedrockProvider(
            model=cfg_expanded.get("model"),
            region=cfg_expanded.get("region"),
        )
        p.name = name
        return p

    logger.warning("Unknown provider type %r for %r — skipping.", ptype, name)
    return None


# ── Registry class ────────────────────────────────────────────────────────────

class ProviderRegistry:
    """Maps provider names to ProviderBase instances and resolves per-agent routing."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderBase] = {}
        self._offline_mode: bool = False

    # ── Loading ───────────────────────────────────────────────────────────────

    @classmethod
    def load(cls) -> ProviderRegistry:
        """Build registry from orchid.defaults.yaml + project .orchid.yaml config."""
        from orchid import config as cfg

        registry = cls()
        registry._offline_mode = (
            os.environ.get("ORCHID_OFFLINE_MODE", "false").lower() in ("true", "1", "yes")
        )

        # Load named provider configs
        provider_cfgs: dict[str, Any] = cfg.get("providers.configured", {})
        for name, pcfg in provider_cfgs.items():
            if not isinstance(pcfg, dict):
                continue
            provider = _instantiate(name, pcfg)
            if provider is not None:
                registry._providers[name] = provider

        # Always ensure built-in "claude" and "local" exist
        if "claude" not in registry._providers:
            from orchid.providers.anthropic import AnthropicProvider
            registry._providers["claude"] = AnthropicProvider()

        if "local" not in registry._providers:
            from orchid.providers.local import LocalProvider
            registry._providers["local"] = LocalProvider()

        logger.debug(
            "Provider registry loaded: %s (offline=%s)",
            list(registry._providers.keys()),
            registry._offline_mode,
        )
        return registry

    # ── Resolution ────────────────────────────────────────────────────────────

    def resolve_name(
        self,
        agent_type: str,
        agent_name: str | None = None,
        task_type: str | None = None,
        task_model: str | None = None,
        cli_override: str | None = None,
    ) -> str:
        """Return the provider name to use, following the 7-layer priority chain.

        Priority (highest to lowest):
          1. CLI --provider flag
          2. Task model: annotation  (model:claude in tasks.md)
          3. Project .orchid.yaml providers.<agent_name>
          4. Machine env ORCHID_<AGENT_TYPE>_PROVIDER
          5. Project .orchid.yaml providers.task_types.<task_type>
          6. Hardcoded task-type default
          7. Hardcoded agent-type default
        """
        if self._offline_mode:
            return "local"

        from orchid import config as cfg

        # 1. CLI --provider flag
        if cli_override and cli_override not in ("", "auto"):
            return cli_override

        # 2. Task model: annotation (model:claude / model:local in tasks.md)
        if task_model and task_model not in ("", "auto"):
            return task_model

        # 3. Project config: providers.<agent_name> (agent_name falls back to agent_type)
        _name_key = agent_name or agent_type
        proj_val = cfg.get(f"providers.{_name_key}")
        if proj_val and isinstance(proj_val, str):
            return proj_val

        # 4. Machine env: ORCHID_<AGENT_TYPE>_PROVIDER
        env_val = os.environ.get(f"ORCHID_{agent_type.upper()}_PROVIDER", "")
        if env_val:
            return env_val

        # 5. Project config: providers.task_types.<task_type>
        if task_type:
            task_val = cfg.get(f"providers.task_types.{task_type}")
            if task_val and isinstance(task_val, str):
                return task_val
            # 6. Hardcoded task-type default
            if task_type in _TASK_TYPE_DEFAULTS:
                return _TASK_TYPE_DEFAULTS[task_type]

        # 7. Hardcoded agent-type default
        return _AGENT_DEFAULTS.get(agent_type, "local")

    def resolve(
        self,
        agent_type: str,
        agent_name: str | None = None,
        task_type: str | None = None,
        task_model: str | None = None,
        cli_override: str | None = None,
    ) -> ProviderBase:
        """Resolve and return a ready ProviderBase for the given context."""
        name = self.resolve_name(agent_type, agent_name, task_type, task_model, cli_override)
        return self._get(name)

    def get_by_key(self, model_key: str) -> ProviderBase:
        """Get a provider directly by name (e.g. 'claude', 'local', 'ollama').

        Used by models.call() for backward compatibility.
        """
        return self._get(model_key)

    def _get(self, name: str) -> ProviderBase:
        if name not in self._providers:
            raise ProviderUnavailableError(
                name,
                f"provider '{name}' not configured",
                f"Add a '{name}' entry under providers.configured in .orchid.yaml",
            )
        provider = self._providers[name]
        if not provider.is_available():
            raise ProviderUnavailableError(
                name,
                provider.availability_detail(),
                provider.fix_suggestion(),
            )
        return provider

    # ── Introspection ─────────────────────────────────────────────────────────

    def all_status(self) -> list[dict[str, Any]]:
        """Return availability status for all configured providers."""
        result = []
        for name, provider in self._providers.items():
            available = provider.is_available()
            entry: dict[str, Any] = {
                "name": name,
                "type": provider.__class__.__name__,
                "available": available,
            }
            if not available:
                entry["missing"] = provider.availability_detail()
                entry["fix"] = provider.fix_suggestion()
            result.append(entry)
        return result

    def set_offline(self, offline: bool) -> None:
        self._offline_mode = offline

    @property
    def offline_mode(self) -> bool:
        return self._offline_mode
