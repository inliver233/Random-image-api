from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

# Shared connection limits for non-proxy upstream fetches (mirrors / direct pximg).
# Proxy URIs need a dedicated client (httpx binds proxy at client construction).
_DEFAULT_LIMITS = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=40,
    keepalive_expiry=30.0,
)

_PROXY_LIMITS = httpx.Limits(
    max_connections=40,
    max_keepalive_connections=16,
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


# Process-local non-proxy client for control-plane outbound (engine events, R2 prewarm)
# when no request-scoped app.state.httpx_client is injected (worker/job handlers).
_control_plane_client: httpx.AsyncClient | None = None


def get_control_plane_http_client() -> httpx.AsyncClient:
    """Lazy process singleton for non-proxy control-plane HTTP.

    Prefer injecting ``app.state.httpx_client`` from the API process. Worker and
    job handlers use this so each publish/prewarm does not open a cold client.
    Callers must **not** aclose the returned client.
    """
    global _control_plane_client
    if _control_plane_client is None:
        _control_plane_client = build_shared_async_client()
    return _control_plane_client


async def aclose_control_plane_http_client() -> None:
    """Shutdown hook — close process control-plane client if created."""
    global _control_plane_client
    client = _control_plane_client
    _control_plane_client = None
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            pass


def reset_control_plane_http_client_for_tests() -> None:
    """Drop control-plane client reference without awaiting close (tests only)."""
    global _control_plane_client
    _control_plane_client = None


class HttpClientLease:
    """One acquired HTTP client reference that must be released exactly once."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        release: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.client = client
        self._release = release
        self._release_task: asyncio.Task[None] | None = None

    async def release(self) -> None:
        if self._release is None:
            return
        if self._release_task is None:
            self._release_task = asyncio.create_task(self._release())
        await asyncio.shield(self._release_task)

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        await self.release()


@dataclass(slots=True)
class _ProxyClientEntry:
    client: httpx.AsyncClient
    leases: int
    last_used: float


class ProxyClientPool:
    """Process-local pool of httpx clients keyed by proxy URI (keepalive reuse).

    httpx binds ``proxy=`` at client construction, so residential paths cannot share
    the non-proxy app.state.httpx_client. This pool reuses one client per proxy URI
    and evicts only released idle/LRU entries. Concurrent active leases may
    temporarily exceed ``max_clients`` rather than closing in-flight requests.
    """

    def __init__(self, *, max_clients: int = 32, idle_ttl_s: float = 60.0) -> None:
        self._max = max(1, int(max_clients))
        self._idle_ttl_s = max(0.0, float(idle_ttl_s))
        self._lock = asyncio.Lock()
        self._clients: dict[tuple[str, int], _ProxyClientEntry] = {}
        self._lru: list[tuple[str, int]] = []

    @property
    def size(self) -> int:
        return len(self._clients)

    def _touch_locked(self, key: tuple[str, int]) -> None:
        try:
            self._lru.remove(key)
        except ValueError:
            pass
        self._lru.append(key)

    def _collect_evictable_locked(
        self,
        *,
        now: float,
        reserve_slot: bool,
    ) -> list[httpx.AsyncClient]:
        close: list[httpx.AsyncClient] = []
        for old_key in list(self._lru):
            entry = self._clients.get(old_key)
            if entry is None:
                self._lru.remove(old_key)
                continue
            if entry.leases > 0:
                continue
            idle = self._idle_ttl_s <= 0.0 or now - entry.last_used >= self._idle_ttl_s
            over_capacity = len(self._clients) > self._max or (
                reserve_slot and len(self._clients) >= self._max
            )
            if not idle and not over_capacity:
                continue
            self._clients.pop(old_key, None)
            self._lru.remove(old_key)
            close.append(entry.client)
            if reserve_slot and len(self._clients) < self._max:
                reserve_slot = False
        return close

    @staticmethod
    async def _close_clients(clients: list[httpx.AsyncClient]) -> None:
        for client in clients:
            try:
                await client.aclose()
            except Exception:
                pass

    async def _release(self, key: tuple[str, int], entry: _ProxyClientEntry) -> None:
        async with self._lock:
            current = self._clients.get(key)
            if current is not entry:
                return
            if entry.leases > 0:
                entry.leases -= 1
            entry.last_used = time.monotonic()
            self._touch_locked(key)
            close = self._collect_evictable_locked(now=entry.last_used, reserve_slot=False)
        await self._close_clients(close)

    async def acquire(
        self,
        proxy_uri: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> HttpClientLease:
        uri = (proxy_uri or "").strip()
        if not uri:
            raise ValueError("proxy_uri is required")
        key = (uri, id(transport) if transport is not None else 0)
        now = time.monotonic()
        await self._lock.acquire()
        try:
            close = self._collect_evictable_locked(
                now=now,
                reserve_slot=self._clients.get(key) is None,
            )
        finally:
            self._lock.release()
        if close:
            close_task = asyncio.create_task(self._close_clients(close))
            await asyncio.shield(close_task)

        await self._lock.acquire()
        try:
            acquired_at = time.monotonic()
            existing = self._clients.get(key)
            if existing is not None:
                existing.leases += 1
                existing.last_used = acquired_at
                self._touch_locked(key)
                entry = existing
            else:
                client = httpx.AsyncClient(
                    transport=transport,
                    proxy=uri,
                    follow_redirects=True,
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    limits=_PROXY_LIMITS,
                )
                entry = _ProxyClientEntry(client=client, leases=1, last_used=acquired_at)
                self._clients[key] = entry
                self._touch_locked(key)
        finally:
            self._lock.release()
        return HttpClientLease(
            entry.client,
            release=lambda: self._release(key, entry),
        )

    async def aclose(self) -> None:
        async with self._lock:
            clients = [entry.client for entry in self._clients.values()]
            self._clients.clear()
            self._lru.clear()
        await self._close_clients(clients)


_proxy_pool: ProxyClientPool | None = None


def get_proxy_client_pool() -> ProxyClientPool:
    """Lazy process singleton (created on first residential proxy use)."""
    global _proxy_pool
    if _proxy_pool is None:
        _proxy_pool = ProxyClientPool()
    return _proxy_pool


async def aclose_proxy_client_pool() -> None:
    """Shutdown hook — close all pooled proxy clients."""
    global _proxy_pool
    pool = _proxy_pool
    _proxy_pool = None
    if pool is not None:
        await pool.aclose()


def reset_proxy_client_pool_for_tests() -> None:
    """Drop pool reference without awaiting close (tests that never opened clients)."""
    global _proxy_pool
    _proxy_pool = None


async def acquire_proxy_client(
    proxy_uri: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    pool: ProxyClientPool | None = None,
) -> HttpClientLease:
    """Acquire a client lease. Callers must release it after buffered requests or stream completion.

    Non-proxy (``proxy_uri`` empty):
    - With ``transport``: short-lived owned client on that transport (tests / custom).
    - Without transport: process control-plane singleton (CF hydrate/OAuth direct egress).
    """
    uri = (proxy_uri or "").strip()
    if not uri:
        if transport is not None:
            client = httpx.AsyncClient(
                transport=transport,
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=_DEFAULT_LIMITS,
            )
            return HttpClientLease(client, release=client.aclose)
        # CF-first / direct origin: reuse process control-plane client (do not aclose).
        return HttpClientLease(get_control_plane_http_client())
    active_pool = pool if pool is not None else get_proxy_client_pool()
    return await active_pool.acquire(uri, transport=transport)
