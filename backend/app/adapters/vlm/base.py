"""Provider port: pipeline stages depend on this, never on vendor HTTP APIs."""

from abc import ABC, abstractmethod


class IVlmProvider(ABC):
    """Generate candidate Mermaid code from an image (callers validate it)."""

    @abstractmethod
    async def diagram_to_mermaid(self, image: bytes, mime_type: str) -> str:
        """Return model output, or raise ProviderError on failure."""
