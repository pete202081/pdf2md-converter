"""Ollama, OpenRouter, OpenAI and Anthropic vision HTTP adapters."""

import base64
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.adapters.vlm.base import IVlmProvider
from app.domain.exceptions import ProviderError

_PROMPT = (
    "Transcribe this flowchart/diagram into Mermaid. Return ONLY a Mermaid code block "
    "with a supported diagram header (flowchart TD, sequenceDiagram, classDiagram, "
    "stateDiagram-v2, or erDiagram). Preserve labels and arrow direction. "
    "Do not invent unreadable details. No prose, HTML, directives, URLs or interactive links."
)


class RetryableProviderError(ProviderError):
    """A transport/rate-limit/server failure for which one retry is useful."""


class PermanentProviderError(ProviderError):
    """Bad credentials or missing model: skip this provider for the job."""


class HttpVlmProvider(IVlmProvider):
    """Shared request logic; a test can inject an httpx.MockTransport client."""

    def __init__(self, timeout: float = 20.0, client: httpx.AsyncClient | None = None) -> None:
        self._timeout = timeout
        self._client = client

    async def _post(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(2),
            wait=wait_exponential(multiplier=0.5, max=2),
            retry=retry_if_exception_type(RetryableProviderError),
            reraise=True,
        ):
            with attempt:
                try:
                    if self._client is None:
                        async with httpx.AsyncClient(timeout=self._timeout) as client:
                            response = await client.post(url, json=payload, headers=headers)
                    else:
                        response = await self._client.post(
                            url, json=payload, headers=headers, timeout=self._timeout
                        )
                except httpx.RequestError as exc:
                    raise RetryableProviderError("Vision provider is unreachable") from exc
                if response.status_code == 429 or response.status_code >= 500:
                    raise RetryableProviderError("Vision provider is temporarily unavailable")
                if response.is_error:
                    # Never include response bodies (may contain secrets/user data).
                    if response.status_code in (401, 403, 404):
                        raise PermanentProviderError(
                            f"Vision provider unavailable ({response.status_code})"
                        )
                    raise ProviderError(
                        f"Vision provider rejected request ({response.status_code})"
                    )
                try:
                    return response.json()
                except ValueError as exc:
                    raise ProviderError("Vision provider returned invalid JSON") from exc
        raise ProviderError("Vision provider did not respond")


class OllamaVisionProvider(HttpVlmProvider):
    """Local Ollama `/api/chat` vision model (base64 images)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout, client)
        self._url = f"{base_url.rstrip('/')}/api/chat"
        self._model = model

    async def diagram_to_mermaid(self, image: bytes, mime_type: str) -> str:
        data = await self._post(
            self._url,
            {
                "model": self._model,
                "stream": False,
                "messages": [
                    {
                        "role": "user",
                        "content": _PROMPT,
                        "images": [base64.b64encode(image).decode("ascii")],
                    }
                ],
                "options": {"temperature": 0},
            },
            {},
        )
        try:
            return str(data["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise ProviderError("Ollama response is missing a message") from exc


class OpenAiVisionProvider(HttpVlmProvider):
    """OpenAI Chat Completions vision endpoint (image data URI)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout, client)
        self._model = model
        self._url = "https://api.openai.com/v1/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def diagram_to_mermaid(self, image: bytes, mime_type: str) -> str:
        data_uri = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
        data = await self._post(
            self._url,
            {
                "model": self._model,
                "max_tokens": 1500,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _PROMPT},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
            },
            self._headers,
        )
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, TypeError, IndexError) as exc:
            raise ProviderError("Chat Completions response is missing a message") from exc


class OpenRouterVisionProvider(OpenAiVisionProvider):
    """OpenRouter's OpenAI-compatible multimodal Chat Completions endpoint.

    Sends a PNG as a data URI. HTTP-Referer and X-OpenRouter-Title are optional attribution
    headers; an absent site URL never adds a misleading referer.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "openai/gpt-4o-mini",
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
        site_url: str = "",
        app_name: str = "PDF2MD Converter",
    ) -> None:
        super().__init__(api_key, model=model, timeout=timeout, client=client)
        self._url = "https://openrouter.ai/api/v1/chat/completions"
        if site_url.strip():
            self._headers["HTTP-Referer"] = site_url.strip()
        if app_name.strip():
            self._headers["X-OpenRouter-Title"] = app_name.strip()


class AnthropicVisionProvider(HttpVlmProvider):
    """Anthropic Messages vision endpoint (base64 image source)."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5",
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout, client)
        self._key = api_key
        self._model = model

    async def diagram_to_mermaid(self, image: bytes, mime_type: str) -> str:
        data = await self._post(
            "https://api.anthropic.com/v1/messages",
            {
                "model": self._model,
                "max_tokens": 1500,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _PROMPT},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime_type,
                                    "data": base64.b64encode(image).decode("ascii"),
                                },
                            },
                        ],
                    }
                ],
            },
            {"x-api-key": self._key, "anthropic-version": "2023-06-01"},
        )
        try:
            return str(next(block["text"] for block in data["content"] if block["type"] == "text"))
        except (KeyError, TypeError, StopIteration) as exc:
            raise ProviderError("Anthropic response is missing a text block") from exc
