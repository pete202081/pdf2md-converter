"""Distributed, fail-closed limits shared by API instances through Redis."""

import hashlib
import ipaddress

from fastapi import HTTPException, Request
from redis.asyncio import Redis

from app.config import Settings


class RateLimiter:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    def remote_ip(self, request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        try:
            trusted = any(
                ipaddress.ip_address(peer) in ipaddress.ip_network(network.strip())
                for network in self._settings.trusted_proxy_cidrs.split(",")
                if network.strip()
            )
            if trusted:
                # nginx OVERWRITES this header; never read X-Forwarded-For supplied by a browser.
                value = request.headers.get("x-real-ip", "")
                return str(ipaddress.ip_address(value)) if value else peer
        except ValueError:
            return peer
        return peer

    async def check(self, category: str, identity: str, *, limit: int, window: int) -> None:
        # Hash identities so usernames/IPs are not stored in Redis key names.
        key = "pdf2md:limit:" + category + ":" + hashlib.sha256(identity.encode()).hexdigest()
        count = await self._redis.incr(key)
        await self._redis.expire(key, window, nx=True)
        if count > limit:
            ttl = max(1, await self._redis.ttl(key))
            raise HTTPException(
                429, "Too many requests. Try again later.", headers={"Retry-After": str(ttl)}
            )

    async def auth(self, request: Request, category: str, username: str = "") -> None:
        settings = self._settings
        limit = settings.signup_limit if category == "signup" else settings.auth_limit
        ip = self.remote_ip(request)
        await self.check(f"{category}:ip", ip, limit=limit, window=900)
        if username:
            normalized = username.strip().lower()
            await self.check(f"{category}:account", normalized, limit=limit, window=900)

    async def costly(self, request: Request, category: str, user_id: int) -> None:
        settings = self._settings
        limit = {
            "upload": settings.upload_limit_hour,
            "review": settings.review_limit_hour,
            "provider": settings.provider_check_limit_hour,
        }[category]
        await self.check(f"{category}:user", str(user_id), limit=limit, window=3600)
        await self.check(f"{category}:ip", self.remote_ip(request), limit=limit * 4, window=3600)
