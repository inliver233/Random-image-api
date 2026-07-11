from __future__ import annotations

import httpx

# Shared connection limits for non-proxy upstream fetches (mirrors / direct pximg).
# Proxy URIs still need per-request clients (httpx binds proxy at client level).
_DEFAULT_LIMITS = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=40,
    keepalive_expiry=30.0,
)


def build_default_async_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(limits=_DEFAULT_LIMITS, retries=0)


def build_shared_async_client(*, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=transport or build_default_async_transport(),
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=_DEFAULT_LIMITS,
    )
