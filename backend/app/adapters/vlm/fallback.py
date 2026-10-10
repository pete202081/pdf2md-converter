"""Local-first provider chain (Strategy + failover)."""

from app.adapters.vlm.base import IVlmProvider
from app.adapters.vlm.http_providers import PermanentProviderError, RetryableProviderError
from app.domain.exceptions import ProviderError
from app.logging import get_logger
from app.services.mermaid_validation import validate_mermaid

logger = get_logger("MermaidProviderChain")


class FallbackMermaidProvider(IVlmProvider):
    """Try providers in order; retryable outages short-circuit for the rest of a job.

    Validate *inside* the chain so an invalid Ollama response can fall back to
    a cloud model rather than incorrectly turning into a successful result.
    A separate chain is built for each conversion job.
    """

    def __init__(self, providers: list[tuple[str, IVlmProvider]]) -> None:
        self._providers = providers
        self._unavailable: set[str] = set()

    async def diagram_to_mermaid(self, image: bytes, mime_type: str) -> str:
        if not self._providers:
            raise ProviderError("No vision provider is configured")
        for name, provider in self._providers:
            if name in self._unavailable:
                continue
            try:
                response = await provider.diagram_to_mermaid(image, mime_type)
                code = validate_mermaid(response)
                logger.info("diagram_converted", provider=name)
                return code
            except (RetryableProviderError, PermanentProviderError) as exc:
                self._unavailable.add(name)
                logger.warning("provider_unavailable", provider=name, error=exc.message)
            except ProviderError as exc:
                logger.warning("provider_failed", provider=name, error=exc.message)
            except Exception:
                logger.exception("provider_crashed", provider=name)
        raise ProviderError("No vision provider produced a valid Mermaid diagram")
