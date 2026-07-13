from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import ParseResult, urljoin, urlparse

import httpx
from starlette.responses import StreamingResponse

from app.core.errors import ApiError, ErrorCode
from app.core.http_client import acquire_proxy_client
from app.core.metrics import UPSTREAM_STREAM_ERRORS_TOTAL
from app.core.pixiv_urls import is_pximg_host

PIXIV_REFERER = "https://www.pixiv.net/"
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 3


def _parse_safe_stream_url(url: str) -> ParseResult:
    raw_url = str(url or "").strip()
    if "\\" in raw_url:
        raise ValueError("stream URL backslashes are not allowed")
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host:
        raise ValueError("stream URL must use HTTPS with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("stream URL userinfo is not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("stream URL port is invalid") from exc
    if port not in {None, 443}:
        raise ValueError("stream URL port is not allowed")
    if host == "localhost":
        raise ValueError("stream URL localhost is not allowed")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("stream URL IP literals are not allowed")
    return parsed


def _resolve_safe_redirect(*, initial: ParseResult, current_url: str, location: str) -> str:
    target_url = urljoin(current_url, str(location or "").strip())
    target = _parse_safe_stream_url(target_url)
    initial_host = (initial.hostname or "").strip().lower().rstrip(".")
    target_host = (target.hostname or "").strip().lower().rstrip(".")
    if is_pximg_host(initial_host):
        if not is_pximg_host(target_host):
            raise ValueError("pximg redirect left the trusted domain")
    elif target_host != initial_host:
        raise ValueError("stream redirect changed hostname")
    return target_url


async def stream_url(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    client: httpx.AsyncClient | None = None,
    proxy: str | None = None,
    cache_control: str,
    referer: str = PIXIV_REFERER,
    timeout_s: float = 30.0,
    range_header: str | None = None,
) -> StreamingResponse:
    """Stream an upstream URL as a Starlette StreamingResponse.

    Connection reuse rules:
    - If ``proxy`` is set: process proxy client pool (httpx binds proxy at client level).
    - Else if shared ``client`` is provided: reuse it (do NOT close on completion).
    - Else: acquire a lease for the control-plane singleton, proxy pool, or an
      owned transport-injected client; release after the stream finishes.
    """
    try:
        initial_url = _parse_safe_stream_url(url)
    except ValueError as exc:
        UPSTREAM_STREAM_ERRORS_TOTAL.inc()
        raise ApiError(code=ErrorCode.UPSTREAM_STREAM_ERROR, message="上游请求失败", status_code=502) from exc

    lease = None
    shared_client = client is not None and not proxy

    if shared_client:
        assert client is not None
        active_client = client
    else:
        lease = await acquire_proxy_client(
            proxy,
            transport=transport,
        )
        active_client = lease.client

    request_headers: dict[str, str] = {}
    if referer:
        request_headers["Referer"] = referer
    if range_header:
        request_headers["Range"] = range_header

    try:
        current_url = str(url or "").strip()
        redirects_followed = 0
        while True:
            request = active_client.build_request(
                "GET",
                current_url,
                headers=request_headers,
                timeout=httpx.Timeout(float(timeout_s), connect=min(10.0, float(timeout_s))),
            )
            upstream = await active_client.send(request, stream=True, follow_redirects=False)
            if upstream.status_code not in _REDIRECT_STATUSES:
                break
            location = str(upstream.headers.get("location") or "").strip()
            await upstream.aclose()
            if redirects_followed >= _MAX_REDIRECTS or not location:
                raise ValueError("upstream redirect limit or location invalid")
            current_url = _resolve_safe_redirect(
                initial=initial_url,
                current_url=current_url,
                location=location,
            )
            redirects_followed += 1
    except httpx.ProxyError as exc:
        UPSTREAM_STREAM_ERRORS_TOTAL.inc()
        if lease is not None:
            await lease.release()
        msg = str(exc).lower()
        if "407" in msg or "proxy authentication" in msg:
            raise ApiError(
                code=ErrorCode.PROXY_AUTH_FAILED,
                message="代理认证失败",
                status_code=502,
            ) from exc
        raise ApiError(
            code=ErrorCode.PROXY_CONNECT_FAILED,
            message="代理连接失败",
            status_code=502,
        ) from exc
    except Exception as exc:
        UPSTREAM_STREAM_ERRORS_TOTAL.inc()
        if lease is not None:
            await lease.release()
        raise ApiError(code=ErrorCode.UPSTREAM_STREAM_ERROR, message="上游请求失败", status_code=502) from exc

    if upstream.status_code not in {200, 206}:
        status = upstream.status_code
        UPSTREAM_STREAM_ERRORS_TOTAL.inc()
        await upstream.aclose()
        if lease is not None:
            await lease.release()
        if status == 403:
            raise ApiError(code=ErrorCode.UPSTREAM_403, message="上游拒绝访问（403）", status_code=502)
        if status == 404:
            raise ApiError(code=ErrorCode.UPSTREAM_404, message="上游资源不存在（404）", status_code=502)
        if status == 429:
            raise ApiError(code=ErrorCode.UPSTREAM_RATE_LIMIT, message="上游触发限流（429）", status_code=502)
        raise ApiError(code=ErrorCode.UPSTREAM_STREAM_ERROR, message="上游错误", status_code=502)

    media_type = upstream.headers.get("content-type") or "application/octet-stream"
    content_length = upstream.headers.get("content-length")

    async def _iter_bytes() -> Any:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        except Exception:
            UPSTREAM_STREAM_ERRORS_TOTAL.inc()
            raise
        finally:
            await upstream.aclose()
            if lease is not None:
                await lease.release()

    accept_ranges = upstream.headers.get("accept-ranges")
    content_range = upstream.headers.get("content-range")

    resp = StreamingResponse(_iter_bytes(), status_code=upstream.status_code, media_type=media_type)
    resp.headers["Cache-Control"] = cache_control
    if content_length:
        resp.headers["Content-Length"] = content_length
    if accept_ranges:
        resp.headers["Accept-Ranges"] = accept_ranges
    if content_range:
        resp.headers["Content-Range"] = content_range
    return resp
