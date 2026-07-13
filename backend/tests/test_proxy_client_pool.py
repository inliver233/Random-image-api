from __future__ import annotations

import asyncio

import httpx

from app.core.http_client import (
    ProxyClientPool,
    acquire_data_plane_client,
    acquire_proxy_client,
    aclose_control_plane_http_client,
    aclose_data_plane_http_client,
    aclose_data_plane_proxy_client_pool,
    aclose_proxy_client_pool,
    build_control_plane_async_client,
    build_data_plane_async_client,
    get_control_plane_http_client,
    get_data_plane_http_client,
    get_data_plane_proxy_client_pool,
    get_proxy_client_pool,
    resolve_http_plane_limits,
    resolve_proxy_pool_max_clients,
    reset_control_plane_http_client_for_tests,
    reset_data_plane_http_client_for_tests,
    reset_data_plane_proxy_client_pool_for_tests,
    reset_proxy_client_pool_for_tests,
)


def setup_function() -> None:
    reset_proxy_client_pool_for_tests()
    reset_data_plane_proxy_client_pool_for_tests()
    reset_control_plane_http_client_for_tests()
    reset_data_plane_http_client_for_tests()


def teardown_function() -> None:
    asyncio.run(aclose_proxy_client_pool())
    asyncio.run(aclose_data_plane_proxy_client_pool())
    asyncio.run(aclose_control_plane_http_client())
    asyncio.run(aclose_data_plane_http_client())
    reset_proxy_client_pool_for_tests()
    reset_data_plane_proxy_client_pool_for_tests()
    reset_control_plane_http_client_for_tests()
    reset_data_plane_http_client_for_tests()


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


def test_data_plane_has_independent_singleton_and_capacity_config() -> None:
    async def _run() -> None:
        control = get_control_plane_http_client()
        data_a = get_data_plane_http_client()
        data_b = get_data_plane_http_client()
        assert data_a is data_b
        assert data_a is not control

        control_limits = resolve_http_plane_limits("control", env={})
        data_limits = resolve_http_plane_limits("data", env={})
        assert (control_limits.max_connections, control_limits.max_keepalive_connections) == (100, 40)
        assert (data_limits.max_connections, data_limits.max_keepalive_connections) == (160, 80)

        overridden = resolve_http_plane_limits(
            "data",
            env={
                "HTTP_DATA_MAX_CONNECTIONS": "12",
                "HTTP_DATA_MAX_KEEPALIVE_CONNECTIONS": "99",
            },
        )
        assert overridden.max_connections == 12
        assert overridden.max_keepalive_connections == 12
        assert resolve_proxy_pool_max_clients("control", env={}) == 32
        assert resolve_proxy_pool_max_clients(
            "data", env={"HTTP_DATA_PROXY_POOL_MAX_CLIENTS": "7"}
        ) == 7

    asyncio.run(_run())


def test_proxy_pool_reuses_same_client() -> None:
    async def _run() -> None:
        pool = ProxyClientPool(max_clients=4)
        lease_a = await pool.acquire("http://user:pass@proxy.example:8080")
        a = lease_a.client
        await lease_a.release()
        lease_b = await pool.acquire("http://user:pass@proxy.example:8080")
        b = lease_b.client
        await lease_b.release()
        assert a is b
        assert pool.size == 1
        await pool.aclose()

    asyncio.run(_run())


def test_proxy_pool_does_not_evict_active_lease() -> None:
    async def _run() -> None:
        pool = ProxyClientPool(max_clients=1)
        lease1 = await pool.acquire("http://p1.example:1")
        c1 = lease1.client
        lease2 = await pool.acquire("http://p2.example:2")
        c2 = lease2.client
        assert pool.size == 2
        assert c1.is_closed is False
        assert c2.is_closed is False

        await lease2.release()
        assert pool.size == 1
        assert c1.is_closed is False
        assert c2.is_closed is True

        await lease1.release()
        await pool.aclose()

    asyncio.run(_run())


def test_acquire_proxy_client_non_proxy_reuses_control_plane() -> None:
    async def _run() -> None:
        # No transport → process control-plane singleton (CF hydrate / OAuth direct).
        lease_a = await acquire_proxy_client(None)
        lease_b = await acquire_proxy_client(None)
        a = lease_a.client
        b = lease_b.client
        assert a is b
        assert a is get_control_plane_http_client()
        await lease_a.release()
        await lease_b.release()

        # Explicit transport still owns a short-lived client (tests / custom).
        transport = httpx.MockTransport(lambda _req: httpx.Response(200))
        owned_lease = await acquire_proxy_client(None, transport=transport)
        owned = owned_lease.client
        assert owned is not a
        await owned_lease.release()
        assert owned.is_closed is True

        pooled_lease = await acquire_proxy_client("http://proxy.example:9")
        pooled = pooled_lease.client
        await pooled_lease.release()
        pooled_lease2 = await acquire_proxy_client("http://proxy.example:9")
        pooled2 = pooled_lease2.client
        await pooled_lease2.release()
        assert pooled is pooled2
        assert get_proxy_client_pool().size == 1

    asyncio.run(_run())


def test_data_plane_proxy_pool_does_not_share_control_client() -> None:
    async def _run() -> None:
        control_lease = await acquire_proxy_client("http://proxy.example:9")
        data_lease = await acquire_data_plane_client("http://proxy.example:9")
        assert control_lease.client is not data_lease.client
        await control_lease.release()
        await data_lease.release()
        assert get_proxy_client_pool().size == 1
        assert get_data_plane_proxy_client_pool().size == 1

    asyncio.run(_run())


def test_blocked_data_plane_request_does_not_block_control_plane() -> None:
    class BlockingTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.started.set()
            await self.release.wait()
            return httpx.Response(200, request=request)

    async def _run() -> None:
        data_transport = BlockingTransport()
        data_client = build_data_plane_async_client(transport=data_transport)
        control_client = build_control_plane_async_client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, request=req))
        )
        data_task = asyncio.create_task(data_client.get("https://data.example.test/image"))
        await data_transport.started.wait()
        control_response = await asyncio.wait_for(
            control_client.get("https://control.example.test/health"),
            timeout=0.25,
        )
        assert control_response.status_code == 200
        data_transport.release.set()
        assert (await data_task).status_code == 200
        await data_client.aclose()
        await control_client.aclose()

    asyncio.run(_run())


def test_proxy_pool_request_timeout_is_not_frozen_by_first_lease() -> None:
    async def _run() -> None:
        pool = ProxyClientPool(max_clients=2)
        lease1 = await pool.acquire("http://proxy.example:9")
        request1 = lease1.client.build_request("GET", "https://example.test/one", timeout=1.25)
        await lease1.release()

        lease2 = await pool.acquire("http://proxy.example:9")
        request2 = lease2.client.build_request("GET", "https://example.test/two", timeout=9.5)
        await lease2.release()

        assert lease1.client is lease2.client
        assert request1.extensions["timeout"]["read"] == 1.25
        assert request2.extensions["timeout"]["read"] == 9.5
        await pool.aclose()

    asyncio.run(_run())


def test_proxy_pool_idle_ttl_closes_released_client() -> None:
    async def _run() -> None:
        pool = ProxyClientPool(max_clients=2, idle_ttl_s=0.0)
        lease = await pool.acquire("http://proxy.example:9")
        client = lease.client
        await lease.release()
        assert pool.size == 0
        assert client.is_closed is True
        await pool.aclose()

    asyncio.run(_run())


def test_proxy_pool_acquire_cancellation_does_not_leak_lease() -> None:
    class BlockingCloseTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.close_started = asyncio.Event()
            self.close_unblock = asyncio.Event()
            self.closed = asyncio.Event()

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request)

        async def aclose(self) -> None:
            self.close_started.set()
            await self.close_unblock.wait()
            self.closed.set()

    async def _run() -> None:
        pool = ProxyClientPool(max_clients=1)
        transport = BlockingCloseTransport()
        lease1 = await pool.acquire("http://p1.example:1", transport=transport)
        await lease1.release()

        acquire_task = asyncio.create_task(pool.acquire("http://p2.example:2"))
        await transport.close_started.wait()
        acquire_task.cancel()
        try:
            await acquire_task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("expected acquire cancellation")

        assert pool.size == 0
        transport.close_unblock.set()
        await transport.closed.wait()
        await pool.aclose()

    asyncio.run(_run())


def test_proxy_pool_release_finishes_after_caller_cancellation() -> None:
    async def _run() -> None:
        pool = ProxyClientPool(max_clients=1, idle_ttl_s=0.0)
        lease = await pool.acquire("http://p1.example:1")
        client = lease.client

        await pool._lock.acquire()
        release_task = asyncio.create_task(lease.release())
        await asyncio.sleep(0)
        release_task.cancel()
        try:
            await release_task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("expected release caller cancellation")
        finally:
            pool._lock.release()

        await lease.release()
        assert pool.size == 0
        assert client.is_closed is True
        await pool.aclose()

    asyncio.run(_run())
