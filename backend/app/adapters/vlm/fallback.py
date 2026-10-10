"""Local-first provider chain (Strategy + failover)."""

from collections.abc import Awaitable, Callable

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

    def __init__(
        self,
        providers: list[tuple[str, IVlmProvider]],
        on_failure: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._providers = providers
        self._unavailable: set[str] = set()
        self._on_failure = on_failure

    async def _record_failure(self, name: str) -> None:
        if self._on_failure is not None:
            try:
                await self._on_failure(name)
            except Exception:
                logger.warning("provider_metric_failed", provider=name)

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
                await self._record_failure(name)
                logger.warning("provider_unavailable", provider=name, error=exc.message)
            except ProviderError as exc:
                await self._record_failure(name)
                logger.warning("provider_failed", provider=name, error=exc.message)
            except Exception:
                await self._record_failure(name)
                logger.exception("provider_crashed", provider=name)
        raise ProviderError("No vision provider produced a valid Mermaid diagram")
