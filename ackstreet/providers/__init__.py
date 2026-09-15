"""Provider registry: map a config entry to a concrete LLM client."""

from __future__ import annotations

from typing import Dict, Optional, Type

from ..config import Config, ProviderConfig
from ..errors import ConfigError
from .anthropic_provider import AnthropicProvider
from .base import (
    BaseProvider,
    Message,
    ProviderResponse,
    StreamCallback,
    ToolCall,
    parse_tool_arguments,
)
from .ollama_provider import OllamaProvider
from .openai_compat import OpenAICompatibleProvider

REGISTRY: Dict[str, Type[BaseProvider]] = {
    "openai": OpenAICompatibleProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "openai-compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
}

#: Alternative provider names that should resolve to a registry entry.
ALIASES: Dict[str, str] = {
    "openrouter": "openai",
    "vllm": "openai",
    "lmstudio": "openai",
    "lm_studio": "openai",
    "llamacpp": "openai",
    "llama.cpp": "openai",
    "groq": "openai",
    "together": "openai",
    "deepseek": "openai",
    "azure": "openai",
    "local": "ollama",
}


def available_types() -> list[str]:
    """Provider ``type`` values that can be instantiated."""
    return sorted({"openai", "anthropic", "ollama"})


def build_provider(spec: ProviderConfig, timeout: float = 120.0) -> BaseProvider:
    """Instantiate the right client for *spec*."""
    kind = (spec.type or "openai").lower().strip()
    kind = ALIASES.get(kind, kind)

    if kind not in REGISTRY:
        raise ConfigError(
            f"Unknown provider type '{spec.type}'. "
            f"Supported types: {', '.join(available_types())}"
        )

    return REGISTRY[kind](
        name=spec.name,
        base_url=spec.base_url,
        model=spec.model,
        api_key=spec.api_key,
        timeout=timeout,
        extra=spec.extra,
        api_key_env=spec.api_key_env,
    )


def provider_from_config(
    config: Config, name: Optional[str] = None, timeout: float = 120.0
) -> BaseProvider:
    """Resolve ``name`` (or the configured default) into a live client."""
    spec = config.resolve_provider(name)
    return build_provider(spec, timeout=timeout)


__all__ = [
    "ANTHROPIC_VERSION",
    "AnthropicProvider",
    "BaseProvider",
    "Message",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "ProviderResponse",
    "REGISTRY",
    "StreamCallback",
    "ToolCall",
    "available_types",
    "build_provider",
    "parse_tool_arguments",
    "provider_from_config",
]

ANTHROPIC_VERSION = "2023-06-01"
