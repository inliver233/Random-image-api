from __future__ import annotations

import asyncio

import httpx

from app.core.http_client import (
    ProxyClientPool,
    acquire_proxy_client,
    aclose_proxy_client_pool,
    get_proxy_client_pool,
    reset_proxy_client_pool_for_tests,
)


def setup_function() -> None:
    reset_proxy_client_pool_for_tests()


def teardown_function() -> None:
    asyncio.run(aclose_proxy_client_pool())
    reset_proxy_client_pool_for_tests()


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


def test_acquire_proxy_client_owns_non_proxy() -> None:
    async def _run() -> None:
        client, owns = await acquire_proxy_client(None, timeout_s=5.0)
        assert owns is True
        assert isinstance(client, httpx.AsyncClient)
        await client.aclose()

        pooled, owns2 = await acquire_proxy_client("http://proxy.example:9", timeout_s=5.0)
        assert owns2 is False
        pooled2, owns3 = await acquire_proxy_client("http://proxy.example:9", timeout_s=5.0)
        assert owns3 is False
        assert pooled is pooled2
        assert get_proxy_client_pool().size == 1

    asyncio.run(_run())
