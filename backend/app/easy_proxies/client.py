from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


def _normalize_base_url(base_url: str) -> str:
    base_url = (base_url or "").strip()
    if not base_url:
        raise ValueError("base_url is required")

    parsed = urlparse(base_url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise ValueError("base_url must be http(s)")

    if not parsed.netloc:
        raise ValueError("base_url must include host")

    return base_url.rstrip("/") + "/"


class EasyProxiesError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class EasyProxiesAuthResult:
    token: str


@asynccontextmanager
async def _easy_proxies_client(
    *,
    transport: httpx.BaseTransport | None,
    timeout_s: float,
    headers: dict[str, str] | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Mock transport → short-lived owned client; else process control-plane singleton.

    Worker auto-refresh and import jobs call auth+export back-to-back; reusing the
    process control-plane client avoids two cold TLS handshakes per cycle. Tests
    that inject MockTransport keep an owned client so the transport is not shared.
    """
    if transport is not None:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout_s, connect=min(10.0, float(timeout_s))),
            follow_redirects=True,
            headers=headers or {},
        ) as client:
            yield client
        return

    from app.core.http_client import get_control_plane_http_client

    client = get_control_plane_http_client()
    # Do not aclose — process singleton closed on worker/API shutdown.
    yield client


async def easy_proxies_auth(
    *,
    base_url: str,
    password: str,
    transport: httpx.BaseTransport | None = None,
    timeout_s: float = 30.0,
) -> EasyProxiesAuthResult:
    base_url_n = _normalize_base_url(base_url)
    password = (password or "").strip()
    if not password:
        raise ValueError("password is required")

    url = urljoin(base_url_n, "api/auth")
    async with _easy_proxies_client(transport=transport, timeout_s=timeout_s) as client:
        resp = await client.post(
            url,
            json={"password": password},
            timeout=httpx.Timeout(timeout_s, connect=min(10.0, float(timeout_s))),
        )

    if resp.status_code != 200:
        raise EasyProxiesError("easy_proxies auth failed", status_code=resp.status_code)

    try:
        data: Any = resp.json()
    except Exception as exc:
        raise EasyProxiesError("easy_proxies auth response is not JSON", status_code=resp.status_code) from exc

    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise EasyProxiesError("easy_proxies auth response missing token", status_code=resp.status_code)

    return EasyProxiesAuthResult(token=token.strip())


async def easy_proxies_export(
    *,
    base_url: str,
    bearer_token: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout_s: float = 30.0,
) -> list[str]:
    base_url_n = _normalize_base_url(base_url)
    url = urljoin(base_url_n, "api/export")

    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    async with _easy_proxies_client(
        transport=transport,
        timeout_s=timeout_s,
        headers=headers,
    ) as client:
        # Control-plane singleton has no per-call default headers; pass Authorization
        # on the request so we do not mutate the shared client.
        resp = await client.get(
            url,
            headers=headers or None,
            timeout=httpx.Timeout(timeout_s, connect=min(10.0, float(timeout_s))),
        )

    if resp.status_code != 200:
        raise EasyProxiesError("easy_proxies export failed", status_code=resp.status_code)

    text = resp.text
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(line)
    return out

