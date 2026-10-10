"""Provider HTTP contract, local-first fallback, and mode selection."""

import base64
import json

import httpx
import pytest

from app.adapters.vlm.base import IVlmProvider
from app.adapters.vlm.factory import build_vlm_provider
from app.adapters.vlm.fallback import FallbackMermaidProvider
from app.adapters.vlm.http_providers import (
    AnthropicVisionProvider,
    OllamaVisionProvider,
    OpenAiVisionProvider,
    OpenRouterVisionProvider,
    PermanentProviderError,
    RetryableProviderError,
)
from app.config import Settings
from app.domain.exceptions import ProviderError

_CODE = "flowchart TD\nA[Start] --> B[Done]"


class StubProvider(IVlmProvider):
    def __init__(self, result: str | Exception) -> None:
        self.result = result
        self.calls = 0

    async def diagram_to_mermaid(self, image: bytes, mime_type: str) -> str:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_fallback_on_invalid_local_response_and_provider_error() -> None:
    local = StubProvider("I cannot read this image")
    cloud = StubProvider(_CODE)
    chain = FallbackMermaidProvider([("ollama", local), ("cloud", cloud)])
    assert await chain.diagram_to_mermaid(b"img", "image/png") == _CODE
    assert (local.calls, cloud.calls) == (1, 1)

    local.result = ProviderError("model missing")
    assert await chain.diagram_to_mermaid(b"img2", "image/png") == _CODE
    assert (local.calls, cloud.calls) == (2, 2)


async def test_retryable_outage_skips_local_for_remaining_images() -> None:
    local = StubProvider(RetryableProviderError("offline"))
    cloud = StubProvider(_CODE)
    chain = FallbackMermaidProvider([("ollama", local), ("cloud", cloud)])
    assert await chain.diagram_to_mermaid(b"one", "image/png") == _CODE
    assert await chain.diagram_to_mermaid(b"two", "image/png") == _CODE
    assert (local.calls, cloud.calls) == (1, 2)


async def test_no_providers_raises_typed_error() -> None:
    with pytest.raises(ProviderError, match="No vision provider"):
        await FallbackMermaidProvider([]).diagram_to_mermaid(b"image", "image/png")


def test_mode_selection_only_enables_available_providers() -> None:
    hybrid = build_vlm_provider(
        Settings(openrouter_api_key="router", openai_api_key="ai", anthropic_api_key="claude")
    )
    assert isinstance(hybrid, FallbackMermaidProvider)
    assert [name for name, _ in hybrid._providers] == [
        "ollama",
        "openrouter",
        "openai",
        "anthropic",
    ]
    cloud = build_vlm_provider(
        Settings(vlm_provider="cloud", openrouter_api_key="router", openai_api_key="ai")
    )
    assert [name for name, _ in cloud._providers] == ["openrouter", "openai"]
    local = build_vlm_provider(
        Settings(vlm_provider="local", openrouter_api_key="router", openai_api_key="ai")
    )
    assert [name for name, _ in local._providers] == ["ollama"]
    router_only = build_vlm_provider(
        Settings(vlm_provider="openrouter", openrouter_api_key="router", openai_api_key="ai")
    )
    assert [name for name, _ in router_only._providers] == ["openrouter"]
    no_key = build_vlm_provider(Settings(vlm_provider="openrouter"))
    assert no_key._providers == []


async def test_ollama_chat_payload_and_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["model"] == "vision-test"
        assert body["stream"] is False
        assert base64.b64decode(body["messages"][0]["images"][0]) == b"PNG"
        return httpx.Response(200, json={"message": {"content": _CODE}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaVisionProvider("http://ollama:11434/", "vision-test", client=client)
        assert await provider.diagram_to_mermaid(b"PNG", "image/png") == _CODE


async def test_openai_vision_payload_and_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        body = json.loads(request.content)
        assert body["messages"][0]["content"][1]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": _CODE}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAiVisionProvider("secret", client=client)
        assert await provider.diagram_to_mermaid(b"PNG", "image/png") == _CODE


async def test_anthropic_vision_payload_and_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        image = body["messages"][0]["content"][1]["source"]
        assert image["media_type"] == "image/png"
        assert base64.b64decode(image["data"]) == b"PNG"
        return httpx.Response(200, json={"content": [{"type": "text", "text": _CODE}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicVisionProvider("secret", client=client)
        assert await provider.diagram_to_mermaid(b"PNG", "image/png") == _CODE


async def test_http_retry_transient_error_then_success() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": "offline"})
        return httpx.Response(200, json={"message": {"content": _CODE}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaVisionProvider("http://ollama:11434", "vision-test", client=client)
        assert await provider.diagram_to_mermaid(b"PNG", "image/png") == _CODE
    assert calls == 2


async def test_http_unauthorized_fails_fast_without_leaking_body() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "private-secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAiVisionProvider("secret", client=client)
        with pytest.raises(ProviderError, match="401") as error:
            await provider.diagram_to_mermaid(b"PNG", "image/png")
    assert "private-secret" not in str(error.value)
    assert calls == 1


async def test_missing_model_is_skipped_for_remaining_images() -> None:
    local = StubProvider(PermanentProviderError("model missing"))
    cloud = StubProvider(_CODE)
    chain = FallbackMermaidProvider([("ollama", local), ("openai", cloud)])
    assert await chain.diagram_to_mermaid(b"one", "image/png") == _CODE
    assert await chain.diagram_to_mermaid(b"two", "image/png") == _CODE
    assert local.calls == 1
    assert cloud.calls == 2


async def test_openrouter_vision_payload_attribution_and_response() -> None:
    """OpenRouter gets a multimodal image data URI and only expected headers."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer router-secret"
        assert request.headers["http-referer"] == "https://pdf2md.example"
        assert request.headers["x-openrouter-title"] == "PDF2MD Test"
        body = json.loads(request.content)
        assert body["model"] == "openai/gpt-4o-mini"
        content = body["messages"][0]["content"]
        assert content[0]["type"] == "text"
        uri = content[1]["image_url"]["url"]
        assert uri.startswith("data:image/png;base64,")
        assert base64.b64decode(uri.split(",", 1)[1]) == b"PNG"
        return httpx.Response(200, json={"choices": [{"message": {"content": _CODE}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterVisionProvider(
            "router-secret",
            client=client,
            site_url="https://pdf2md.example",
            app_name="PDF2MD Test",
        )
        assert await provider.diagram_to_mermaid(b"PNG", "image/png") == _CODE


async def test_openrouter_attribution_headers_are_optional() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "http-referer" not in request.headers
        assert "x-openrouter-title" not in request.headers
        return httpx.Response(200, json={"choices": [{"message": {"content": _CODE}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterVisionProvider("router-secret", client=client, app_name="")
        assert await provider.diagram_to_mermaid(b"PNG", "image/png") == _CODE


async def test_local_failure_falls_back_to_openrouter_before_openai() -> None:
    local = StubProvider(ProviderError("Ollama model is missing"))
    router = StubProvider(_CODE)
    openai = StubProvider(_CODE)
    chain = FallbackMermaidProvider([("ollama", local), ("openrouter", router), ("openai", openai)])
    assert await chain.diagram_to_mermaid(b"PNG", "image/png") == _CODE
    assert (local.calls, router.calls, openai.calls) == (1, 1, 0)


async def test_invalid_openrouter_response_falls_back_to_openai() -> None:
    router = StubProvider("I cannot interpret the image")
    openai = StubProvider(_CODE)
    chain = FallbackMermaidProvider([("openrouter", router), ("openai", openai)])
    assert await chain.diagram_to_mermaid(b"PNG", "image/png") == _CODE
    assert (router.calls, openai.calls) == (1, 1)
