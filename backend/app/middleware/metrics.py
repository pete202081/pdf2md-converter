"""Request metrics: route templates only, never user paths or identifying values."""

import time

from fastapi import Request
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

REQUESTS = Counter("pdf2md_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram(
    "pdf2md_http_request_duration_seconds",
    "HTTP latency",
    ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 5, 30),
)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)
        start = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            template = getattr(route, "path", "_unmatched")
            # 'route' comes from the server router, not any client-provided value.
            REQUESTS.labels(request.method, template, str(status)).inc()
            LATENCY.labels(request.method, template).observe(time.monotonic() - start)
