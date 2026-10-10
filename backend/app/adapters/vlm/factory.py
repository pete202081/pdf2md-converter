"""Build the configured local-first provider chain (composition root helper)."""

from collections.abc import Awaitable, Callable

from app.adapters.vlm.base import IVlmProvider
from app.adapters.vlm.fallback import FallbackMermaidProvider
from app.adapters.vlm.http_providers import (
    AnthropicVisionProvider,
    OllamaVisionProvider,
    OpenAiVisionProvider,
    OpenRouterVisionProvider,
)
from app.config import Settings


def build_vlm_provider(
    settings: Settings, on_failure: Callable[[str], Awaitable[None]] | None = None
) -> IVlmProvider:
    """Ollama → OpenRouter → OpenAI → Anthropic, as configured."""
    providers: list[tuple[str, IVlmProvider]] = []
    timeout = settings.vlm_timeout_seconds
    if settings.vlm_provider in ("hybrid", "local"):
        providers.append(
            (
                "ollama",
                OllamaVisionProvider(
                    settings.ollama_base_url, settings.ollama_vision_model, timeout=timeout
                ),
            )
        )
    if (
        settings.vlm_provider in ("hybrid", "cloud", "openrouter")
        and settings.openrouter_api_key.strip()
    ):
        providers.append(
            (
                "openrouter",
                OpenRouterVisionProvider(
                    settings.openrouter_api_key,
                    model=settings.openrouter_vision_model,
                    timeout=timeout,
                    site_url=settings.openrouter_site_url,
                    app_name=settings.openrouter_app_name,
                ),
            )
        )
    if settings.vlm_provider in ("hybrid", "cloud"):
        if settings.openai_api_key.strip():
            providers.append(
                (
                    "openai",
                    OpenAiVisionProvider(
                        settings.openai_api_key,
                        model=settings.openai_vision_model,
                        timeout=timeout,
                    ),
                )
            )
        if settings.anthropic_api_key.strip():
            providers.append(
                (
                    "anthropic",
                    AnthropicVisionProvider(
                        settings.anthropic_api_key,
                        model=settings.anthropic_vision_model,
                        timeout=timeout,
                    ),
                )
            )
    return FallbackMermaidProvider(providers, on_failure=on_failure)
