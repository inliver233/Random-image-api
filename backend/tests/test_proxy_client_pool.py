from __future__ import annotations

import asyncio

import httpx

from app.core.http_client import (
    ProxyClientPool,
    acquire_proxy_client,
    aclose_control_plane_http_client,
    aclose_proxy_client_pool,
    get_control_plane_http_client,
    get_proxy_client_pool,
    reset_control_plane_http_client_for_tests,
    reset_proxy_client_pool_for_tests,
)


def setup_function() -> None:
    reset_proxy_client_pool_for_tests()
    reset_control_plane_http_client_for_tests()


def teardown_function() -> None:
    asyncio.run(aclose_proxy_client_pool())
    asyncio.run(aclose_control_plane_http_client())
    reset_proxy_client_pool_for_tests()
    reset_control_plane_http_client_for_tests()


def test_control_plane_http_client_reuses_singleton() -> None:
    async def _run() -> None:
        a = get_control_plane_http_client()
        b = get_control_plane_http_client()
        assert a is b
        assert isinstance(a, httpx.AsyncClient)
        await aclose_control_plane_http_client()
        c = get_control_plane_http_client()
        assert c is not a

    asyncio.run(_run())


def test_proxy_pool_reuses_same_client() -> None:
    async def _run() -> None:
        pool = ProxyClientPool(max_clients=4)
        a = await pool.get("http://user:pass@proxy.example:8080")
        b = await pool.get("http://user:pass@proxy.example:8080")
        assert a is b
        assert pool.size == 1
        await pool.aclose()

    asyncio.run(_run())


def test_proxy_pool_evicts_lru() -> None:
    async def _run() -> None:
        pool = ProxyClientPool(max_clients=2)
        c1 = await pool.get("http://p1.example:1")
        c2 = await pool.get("http://p2.example:2")
        assert pool.size == 2
        await pool.get("http://p3.example:3")
        assert pool.size == 2
        # p1 was LRU and should be gone; re-get creates a new client.
        c1b = await pool.get("http://p1.example:1")
        assert c1b is not c1
        # touching p2 after p3 kept p2; after p1 re-insert p2 or p3 may have been evicted
        assert pool.size == 2
        await pool.aclose()

    asyncio.run(_run())


def test_acquire_proxy_client_non_proxy_reuses_control_plane() -> None:
    async def _run() -> None:
        # No transport → process control-plane singleton (CF hydrate / OAuth direct).
        a, owns_a = await acquire_proxy_client(None, timeout_s=5.0)
        b, owns_b = await acquire_proxy_client(None, timeout_s=5.0)
        assert owns_a is False
        assert owns_b is False
        assert a is b
        assert a is get_control_plane_http_client()

        # Explicit transport still owns a short-lived client (tests / custom).
        transport = httpx.MockTransport(lambda _req: httpx.Response(200))
        owned, owns_owned = await acquire_proxy_client(None, timeout_s=5.0, transport=transport)
        assert owns_owned is True
        assert owned is not a
        await owned.aclose()

        pooled, owns2 = await acquire_proxy_client("http://proxy.example:9", timeout_s=5.0)
        assert owns2 is False
        pooled2, owns3 = await acquire_proxy_client("http://proxy.example:9", timeout_s=5.0)
        assert owns3 is False
        assert pooled is pooled2
        assert get_proxy_client_pool().size == 1

    asyncio.run(_run())
