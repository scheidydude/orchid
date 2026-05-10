"""ProviderRegistry - 5-layer provider resolution for Orchid agents."""

from __future__ import annotations

import logging
import os
from typing import Any

from orchid.providers.base import ProviderBase, ProviderUnavailableError

logger = logging.getLogger(__name__)

# - Registry singleton -

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


# - Resolution defaults -

_AGENT_DEFAULTS: dict[str, str] = {
    "orchestrator":    "claude",
    "reviewer":        "claude",
    "developer":       "local",
    "researcher":      "local",
    "base":            "local",
    "discussion":      "claude",
    "product_manager": "claude",
    "project_manager": "claude",
}

_TASK_TYPE_DEFAULTS: dict[str, str] = {
    "orchestrate":   "claude",
    "review":        "claude",
    "critique":      "claude",
    "plan":          "claude",
    "synthesize":    "claude",
    "rollup":        "claude",
    "draft":         "local",
    "code_generate": "local",
    "summarize":     "local",
    "search":        "local",
    "transform":     "local",
    "research":      "local",
}


# - Provider instantiation helpers -

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
        p.name = name
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

    logger.warning("Unknown provider type %r for %r - skipping.", ptype, name)
    return None


# - Registry class -

class ProviderRegistry:
    """Maps provider names to ProviderBase instances and resolves per-agent routing."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderBase] = {}
        self._offline_mode: bool = False

    @classmethod
    def load(cls) -> ProviderRegistry:
        """Build registry from orchid.defaults.yaml + project .orchid.yaml config."""
        from orchid import config as cfg

        registry = cls()
        registry._offline_mode = (
            os.environ.get("ORCHID_OFFLINE_MODE", "false").lower() in ("true", "1", "yes")
        )

        provider_cfgs: dict[str, Any] = cfg.get("providers.configured", {})
        for name, pcfg in provider_cfgs.items():
            if not isinstance(pcfg, dict):
                continue
            provider = _instantiate(name, pcfg)
            if provider is not None:
                registry._providers[name] = provider

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

    # - Resolution -

    def resolve_name(
        self,
        agent_type: str,
        agent_name: str | None = None,
        task_type: str | None = None,
        task_model: str | None = None,
        cli_override: str | None = None,
        task_title: str = "",
        user_api_keys: dict[str, str] | None = None,
    ) -> str:
        """Return the provider name to use, following the 8-layer priority chain.

        Priority (highest to lowest):
          1. CLI --provider flag
          2. Project .orchid.yaml providers.<agent_name>
          3. Project .orchid.yaml providers.task_types.<task_type>
          4. Task model: annotation
          5. Machine env ORCHID_<AGENT_TYPE>_PROVIDER
          5b. Keyword-heuristic escalation
          6. Config task-type default
          7. Config agent-type default

        Args:
            user_api_keys: Optional mapping of provider name -> API key.
                Only applied when returning a ProviderBase via :meth:`resolve`.

        Returns:
            The resolved provider name.
        """
        if self._offline_mode:
            return "local"

        from orchid import config as cfg

        # 1. CLI --provider flag
        if cli_override and cli_override not in ("", "auto"):
            return cli_override

        # 2. Project config: providers.<agent_name>
        _name_key = agent_name or agent_type
        proj_val = cfg.get(f"providers.{_name_key}")
        if proj_val and isinstance(proj_val, str):
            return proj_val

        # 3. Project config: providers.task_types.<task_type>
        if task_type:
            task_val = cfg.get(f"providers.task_types.{task_type}")
            if task_val and isinstance(task_val, str):
                return task_val

        # 4. Task model: annotation
        if task_model and task_model not in ("", "auto"):
            return task_model

        # 5. Machine env: ORCHID_<AGENT_TYPE>_PROVIDER
        env_val = os.environ.get(f"ORCHID_{agent_type.upper()}_PROVIDER", "")
        if env_val:
            return env_val

        # 5b. Keyword-heuristic escalation
        if task_title:
            escalation_cfg = cfg.get("routing.escalation", {})
            if escalation_cfg.get("enabled", True):
                threshold = int(escalation_cfg.get("threshold", 1))
                keywords = escalation_cfg.get("keywords", [])
                title_lower = task_title.lower()
                if sum(1 for kw in keywords if kw in title_lower) >= threshold:
                    return cfg.get("providers.agent_defaults.orchestrator", "claude")

        # 6. Config-driven task-type default
        if task_type:
            cfg_task_default = cfg.get(f"providers.task_type_defaults.{task_type}")
            if cfg_task_default and isinstance(cfg_task_default, str):
                return cfg_task_default
            if task_type in _TASK_TYPE_DEFAULTS:
                return _TASK_TYPE_DEFAULTS[task_type]

        # 7. Config-driven agent-type default
        cfg_agent_default = cfg.get(f"providers.agent_defaults.{agent_type}")
        if cfg_agent_default and isinstance(cfg_agent_default, str):
            return cfg_agent_default
        return _AGENT_DEFAULTS.get(agent_type, "local")

    def resolve_chain(
        self,
        agent_type: str,
        agent_name: str | None = None,
        task_type: str | None = None,
        task_model: str | None = None,
        cli_override: str | None = None,
        task_title: str = "",
    ) -> tuple[str, list[str]]:
        """Return (primary_provider_name, [ordered_fallback_names]).

        Fallback list is read from:
          providers.task_types.<task_type>.fallback  (dict form: {name: ..., fallback: [...]})
          providers.<agent_name>.fallback             (dict form)
        Unknown or duplicate names are filtered out.
        """
        primary = self.resolve_name(
            agent_type, agent_name, task_type, task_model, cli_override, task_title,
        )
        from orchid import config as cfg
        fallback: list[str] = []

        # task_type config has highest priority for fallback list
        if task_type:
            task_cfg = cfg.get(f"providers.task_types.{task_type}")
            if isinstance(task_cfg, dict):
                fallback = list(task_cfg.get("fallback", []) or [])

        # agent-level config as secondary source
        if not fallback:
            _name_key = agent_name or agent_type
            agent_cfg = cfg.get(f"providers.{_name_key}")
            if isinstance(agent_cfg, dict):
                fallback = list(agent_cfg.get("fallback", []) or [])

        # filter: drop primary, drop unknown, deduplicate
        seen: set[str] = {primary}
        cleaned: list[str] = []
        for name in fallback:
            if isinstance(name, str) and name not in seen and name in self._providers:
                cleaned.append(name)
                seen.add(name)

        return primary, cleaned

    def resolve(
        self,
        agent_type: str,
        agent_name: str | None = None,
        task_type: str | None = None,
        task_model: str | None = None,
        cli_override: str | None = None,
        task_title: str = "",
        user_api_keys: dict[str, str] | None = None,
    ) -> ProviderBase:
        """Resolve and return a ready ProviderBase for the given context.

        If user_api_keys is provided and the resolved provider name is a key
        in that dict, the provider's api_key attribute is overwritten with
        the user-supplied value.

        Args:
            user_api_keys: Optional mapping of provider name -> API key.

        Returns:
            A ready-to-use ProviderBase instance.
        """
        name = self.resolve_name(
            agent_type,
            agent_name,
            task_type,
            task_model,
            cli_override,
            task_title,
        )
        provider = self._get(name)

        # Apply per-user API key override when requested
        if user_api_keys and name in user_api_keys:
            user_key = user_api_keys[name]
            if hasattr(provider, "api_key"):
                provider.api_key = user_key
            else:
                logger.debug(
                    "Provider %r has no 'api_key' attribute - user key for %s ignored.",
                    name, name,
                )

        return provider

    def get_by_key(self, model_key: str) -> ProviderBase:
        """Get a provider directly by name (e.g. 'claude', 'local', 'ollama')."""
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

    # - Introspection -

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
