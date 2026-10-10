"""Redis limits are shared across instances and forwarded headers are conditional."""

import fakeredis.aioredis
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import Settings
from app.services.rate_limits import RateLimiter


def request(peer: str, headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
            ],
            "client": (peer, 4242),
        }
    )


@pytest.mark.asyncio
async def test_shared_ip_and_account_limits_with_retry_after() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    first = RateLimiter(redis, Settings(auth_limit=2))
    second = RateLimiter(redis, Settings(auth_limit=2))
    await first.auth(request("1.2.3.4"), "login", "alice")
    await second.auth(request("1.2.3.4"), "login", "alice")
    with pytest.raises(HTTPException) as error:
        await first.auth(request("1.2.3.4"), "login", "alice")
    assert error.value.status_code == 429
    assert int(error.value.headers["Retry-After"]) > 0
    with pytest.raises(HTTPException):
        await first.auth(request("5.6.7.8"), "login", "alice")
    await redis.aclose()


def test_client_cannot_spoof_ip_without_trusted_proxy() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    spoof = request("198.51.100.10", {"x-real-ip": "203.0.113.2", "x-forwarded-for": "203.0.113.3"})
    assert RateLimiter(redis, Settings()).remote_ip(spoof) == "198.51.100.10"
    allowed = Settings(trusted_proxy_cidrs="198.51.100.0/24")
    assert RateLimiter(redis, allowed).remote_ip(spoof) == "203.0.113.2"
    assert (
        RateLimiter(redis, allowed).remote_ip(request("198.51.100.10", {"x-real-ip": "not-an-ip"}))
        == "198.51.100.10"
    )
