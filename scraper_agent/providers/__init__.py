"""Provider registry."""

from __future__ import annotations

from scraper_agent.config import Settings
from scraper_agent.providers.base import (
    LLMProvider,
    ProviderError,
    Usage,
    estimate_cost_usd,
)

PROVIDERS = ("openai", "ollama")


def get_provider(
    name: str | None = None,
    model: str | None = None,
    settings: Settings | None = None,
) -> LLMProvider:
    """Build the provider named `name`, defaulting to settings/env."""
    settings = settings or Settings.from_env()
    name = (name or settings.provider).lower().strip()

    if name == "openai":
        from scraper_agent.providers.openai_provider import OpenAIProvider

        return OpenAIProvider(model=model or settings.openai_model)

    if name == "ollama":
        from scraper_agent.providers.ollama_provider import OllamaProvider

        return OllamaProvider(
            model=model or settings.ollama_model, host=settings.ollama_host
        )

    raise ProviderError(
        f"Unknown provider {name!r}. Available: {', '.join(PROVIDERS)}."
    )


__all__ = [
    "LLMProvider",
    "ProviderError",
    "Usage",
    "PROVIDERS",
    "estimate_cost_usd",
    "get_provider",
]
