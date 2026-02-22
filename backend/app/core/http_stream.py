from __future__ import annotations

from typing import Any

import httpx
from starlette.responses import StreamingResponse

from app.core.errors import ApiError, ErrorCode
from app.core.metrics import UPSTREAM_STREAM_ERRORS_TOTAL

PIXIV_REFERER = "https://www.pixiv.net/"


async def stream_url(
    url: str,
    *,
    transport: httpx.BaseTransport | None = None,
    proxy: str | None = None,
    cache_control: str,
    referer: str = PIXIV_REFERER,
    timeout_s: float = 30.0,
    range_header: str | None = None,
) -> StreamingResponse:
    client = httpx.AsyncClient(
        transport=transport,
        proxy=proxy,
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_s, connect=10.0),
    )

    request_headers: dict[str, str] = {}
    if referer:
        request_headers["Referer"] = referer
    if range_header:
        request_headers["Range"] = range_header
    request = client.build_request("GET", url, headers=request_headers)

    try:
        upstream = await client.send(request, stream=True)
    except httpx.ProxyError as exc:
        UPSTREAM_STREAM_ERRORS_TOTAL.inc()
        await client.aclose()
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
        await client.aclose()
        raise ApiError(code=ErrorCode.UPSTREAM_STREAM_ERROR, message="上游请求失败", status_code=502) from exc

    if upstream.status_code not in {200, 206}:
        status = upstream.status_code
        UPSTREAM_STREAM_ERRORS_TOTAL.inc()
        await upstream.aclose()
        await client.aclose()
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
            await client.aclose()

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
