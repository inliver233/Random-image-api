from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core.http_client import (
    aclose_control_plane_http_client,
    get_control_plane_http_client,
    reset_control_plane_http_client_for_tests,
)
from app.easy_proxies.client import EasyProxiesError, easy_proxies_auth, easy_proxies_export


def test_easy_proxies_auth_success_returns_token() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert str(req.url) == "http://easy-proxies:9090/api/auth"
        assert req.headers.get("content-type", "").startswith("application/json")
        assert req.content == b'{\"password\":\"pw\"}'
        return httpx.Response(200, json={"token": "tok"})

    result = asyncio.run(
        easy_proxies_auth(
            base_url="http://easy-proxies:9090",
            password="pw",
            transport=httpx.MockTransport(handler),
        )
    )
    assert result.token == "tok"


def test_easy_proxies_export_sends_bearer_token_header() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        assert str(req.url) == "http://easy-proxies:9090/api/export"
        assert req.headers.get("Authorization") == "Bearer tok"
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, content=b"http://1.2.3.4:1234\n\nsocks5://5.6.7.8:1080\n")

    out = asyncio.run(
        easy_proxies_export(
            base_url="http://easy-proxies:9090",
            bearer_token="tok",
            transport=httpx.MockTransport(handler),
        )
    )
    assert out == ["http://1.2.3.4:1234", "socks5://5.6.7.8:1080"]


def test_easy_proxies_export_error_does_not_echo_token() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(EasyProxiesError) as excinfo:
        asyncio.run(
            easy_proxies_export(
                base_url="http://easy-proxies:9090",
                bearer_token="tok_secret",
                transport=httpx.MockTransport(handler),
            )
        )
    assert excinfo.value.status_code == 401
    assert "tok_secret" not in str(excinfo.value)


def test_easy_proxies_reuses_control_plane_client_without_transport(monkeypatch) -> None:
    """Worker import path (no MockTransport) must not open a cold client per call."""
    reset_control_plane_http_client_for_tests()
    shared = get_control_plane_http_client()
    seen: list[httpx.AsyncClient] = []

    async def fake_post(self, url, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(self)
        return httpx.Response(200, json={"token": "tok"}, request=httpx.Request("POST", str(url)))

    async def fake_get(self, url, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(self)
        assert kwargs.get("headers", {}).get("Authorization") == "Bearer tok"
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            content=b"http://1.2.3.4:1234\n",
            request=httpx.Request("GET", str(url)),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post, raising=True)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get, raising=True)

    async def _run() -> None:
        auth = await easy_proxies_auth(base_url="http://easy-proxies:9090", password="pw")
        assert auth.token == "tok"
        out = await easy_proxies_export(base_url="http://easy-proxies:9090", bearer_token="tok")
        assert out == ["http://1.2.3.4:1234"]
        await aclose_control_plane_http_client()
        reset_control_plane_http_client_for_tests()

    asyncio.run(_run())
    assert len(seen) == 2
    assert seen[0] is shared
    assert seen[1] is shared

