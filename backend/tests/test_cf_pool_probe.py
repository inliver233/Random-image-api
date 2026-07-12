from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx

from app.core.cf_api_proxy import is_cf_base_cooling, reset_cf_base_cooldown_for_tests
from app.core.cf_pool_probe import probe_cf_pool_bases, probe_cf_worker_base
from app.core.image_edge import is_image_edge_base_cooling, reset_image_edge_base_cooldown_for_tests


def _json_response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("GET", "https://example.workers.dev/healthz"),
    )


def test_probe_cf_worker_base_ok_api_clears_cooldown() -> None:
    reset_cf_base_cooldown_for_tests()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        return_value=_json_response(
            200,
            {
                "ok": True,
                "service": "random-image-api-proxy",
                "secret_configured": True,
            },
        )
    )

    async def _run() -> dict:
        return await probe_cf_worker_base(
            client,
            kind="api",
            base_url="https://api-a.example.workers.dev/",
            timeout_s=1.0,
        )

    out = asyncio.run(_run())
    assert out["ok"] is True
    assert out["base_url"] == "https://api-a.example.workers.dev"
    assert out["service"] == "random-image-api-proxy"
    assert out["secret_configured"] is True
    assert out["status_code"] == 200
    assert is_cf_base_cooling("https://api-a.example.workers.dev") is False
    client.get.assert_awaited_once()
    reset_cf_base_cooldown_for_tests()


def test_probe_cf_worker_base_hard_fail_records_cooldown() -> None:
    reset_cf_base_cooldown_for_tests()
    reset_image_edge_base_cooldown_for_tests()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))

    async def _run() -> dict:
        return await probe_cf_worker_base(
            client,
            kind="image",
            base_url="https://img-a.example.workers.dev",
            timeout_s=1.0,
        )

    out = asyncio.run(_run())
    assert out["ok"] is False
    assert out["error"] == "ConnectError"
    assert is_image_edge_base_cooling("https://img-a.example.workers.dev") is True
    reset_image_edge_base_cooldown_for_tests()


def test_probe_cf_worker_base_status_not_ok() -> None:
    reset_cf_base_cooldown_for_tests()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        return_value=httpx.Response(
            503,
            text="unavailable",
            request=httpx.Request("GET", "https://api-b.example.workers.dev/healthz"),
        )
    )

    async def _run() -> dict:
        return await probe_cf_worker_base(
            client,
            kind="api",
            base_url="https://api-b.example.workers.dev",
        )

    out = asyncio.run(_run())
    assert out["ok"] is False
    assert out["status_code"] == 503
    assert is_cf_base_cooling("https://api-b.example.workers.dev") is True
    reset_cf_base_cooldown_for_tests()


def test_probe_cf_pool_bases_preserves_order_and_dedupes() -> None:
    reset_cf_base_cooldown_for_tests()
    client = AsyncMock(spec=httpx.AsyncClient)

    async def _get(url: str, timeout: float = 3.0) -> httpx.Response:
        _ = timeout
        _ = url
        return _json_response(
            200,
            {"ok": True, "service": "random-image-api-proxy", "secret_configured": True},
        )

    client.get = AsyncMock(side_effect=_get)

    async def _run() -> list:
        return await probe_cf_pool_bases(
            client,
            kind="api",
            bases=[
                "https://a.example.workers.dev/",
                "https://b.example.workers.dev",
                "https://a.example.workers.dev",
            ],
        )

    results = asyncio.run(_run())
    assert [r["base_url"] for r in results] == [
        "https://a.example.workers.dev",
        "https://b.example.workers.dev",
    ]
    assert all(r["ok"] for r in results)
    reset_cf_base_cooldown_for_tests()


def test_probe_empty_base_url() -> None:
    client = MagicMock(spec=httpx.AsyncClient)

    async def _run() -> dict:
        return await probe_cf_worker_base(client, kind="api", base_url="   ")

    out = asyncio.run(_run())
    assert out["ok"] is False
    assert out["error"] == "empty_base_url"


def test_probe_cf_worker_base_api_secret_not_configured() -> None:
    """healthz may be ok:true with empty PROXY_SECRET — not cutover-ready, no cooldown clear."""
    reset_cf_base_cooldown_for_tests()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        return_value=_json_response(
            200,
            {
                "ok": True,
                "service": "random-image-api-proxy",
                "secret_configured": False,
            },
        )
    )

    async def _run() -> dict:
        return await probe_cf_worker_base(
            client,
            kind="api",
            base_url="https://api-nosecret.example.workers.dev/",
            timeout_s=1.0,
        )

    out = asyncio.run(_run())
    assert out["ok"] is False
    assert out["error"] == "secret_not_configured"
    assert out["secret_configured"] is False
    assert out["body_ok"] is True
    # Config gap must not open egress cooldown (would demote a good base wrongly).
    assert is_cf_base_cooling("https://api-nosecret.example.workers.dev") is False
    reset_cf_base_cooldown_for_tests()


def test_probe_cf_worker_base_image_secret_not_configured() -> None:
    """img-worker healthz secret_configured=false → not cutover-ready, no cooldown."""
    reset_image_edge_base_cooldown_for_tests()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        return_value=_json_response(
            200,
            {
                "ok": True,
                "service": "random-image-edge",
                "secret_configured": False,
            },
        )
    )

    async def _run() -> dict:
        return await probe_cf_worker_base(
            client,
            kind="image",
            base_url="https://img-nosecret.example.workers.dev/",
            timeout_s=1.0,
        )

    out = asyncio.run(_run())
    assert out["ok"] is False
    assert out["error"] == "secret_not_configured"
    assert out["secret_configured"] is False
    assert is_image_edge_base_cooling("https://img-nosecret.example.workers.dev") is False
    reset_image_edge_base_cooldown_for_tests()
