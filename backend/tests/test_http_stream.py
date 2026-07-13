from __future__ import annotations

import asyncio

import httpx

from app.core.errors import ApiError, ErrorCode
from app.core.http_stream import PIXIV_REFERER, stream_url


class _DummyStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _BlockingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False
        self.started = asyncio.Event()
        self.unblock = asyncio.Event()

    async def __aiter__(self):
        yield b"first"
        self.started.set()
        await self.unblock.wait()
        yield b"second"

    async def aclose(self) -> None:
        self.closed = True
        self.unblock.set()


def test_stream_url_uses_streaming(monkeypatch) -> None:
    sent_stream_flag: bool | None = None
    sent_follow_redirects: bool | None = None
    dummy_stream = _DummyStream([b"abc", b"def"])

    async def fake_send(self, request: httpx.Request, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal sent_follow_redirects, sent_stream_flag
        sent_stream_flag = bool(kwargs.get("stream"))
        sent_follow_redirects = kwargs.get("follow_redirects")
        return httpx.Response(
            200,
            headers={"Content-Type": "application/octet-stream"},
            stream=dummy_stream,
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send, raising=True)

    async def _run() -> bytes:
        resp = await stream_url("https://example.test/big.bin", cache_control="no-store")
        chunks: list[bytes] = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        if resp.background is not None:
            await resp.background()
        return b"".join(chunks)

    body = asyncio.run(_run())
    assert sent_stream_flag is True
    assert sent_follow_redirects is False
    assert body == b"abcdef"
    assert dummy_stream.closed is True


def test_stream_url_follows_only_validated_pximg_redirects() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.host == "i.pximg.net":
            return httpx.Response(302, headers={"Location": "https://i-cf.pximg.net/final.jpg"}, request=request)
        assert request.headers["Referer"] == PIXIV_REFERER
        assert request.headers["Range"] == "bytes=1-2"
        return httpx.Response(206, content=b"ok", request=request)

    async def _run() -> bytes:
        resp = await stream_url(
            "https://i.pximg.net/start.jpg",
            transport=httpx.MockTransport(handler),
            cache_control="no-store",
            range_header="bytes=1-2",
        )
        chunks = [chunk async for chunk in resp.body_iterator]
        return b"".join(chunks)

    assert asyncio.run(_run()) == b"ok"
    assert requested == [
        "https://i.pximg.net/start.jpg",
        "https://i-cf.pximg.net/final.jpg",
    ]


def test_stream_url_rejects_cross_boundary_redirect_before_request() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"}, request=request)

    async def _run() -> None:
        try:
            await stream_url(
                "https://i.pximg.net/start.jpg",
                transport=httpx.MockTransport(handler),
                cache_control="no-store",
            )
        except ApiError as exc:
            assert exc.code == ErrorCode.UPSTREAM_STREAM_ERROR
            assert exc.status_code == 502
        else:
            raise AssertionError("expected redirect rejection")

    asyncio.run(_run())
    assert requested == ["https://i.pximg.net/start.jpg"]


def test_stream_url_rejects_unsafe_redirect_variants() -> None:
    targets = [
        "https://evilpximg.net/collect",
        "https://pximg.net.evil/collect",
        "https://user:pass@i.pximg.net/collect",
        "https://i.pximg.net:444/collect",
        "https://127.0.0.1/collect",
        "   ",
    ]

    async def _run(target: str) -> list[str]:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(302, headers={"Location": target}, request=request)

        try:
            await stream_url(
                "https://i.pximg.net/start.jpg",
                transport=httpx.MockTransport(handler),
                cache_control="no-store",
            )
        except ApiError as exc:
            assert exc.code == ErrorCode.UPSTREAM_STREAM_ERROR
        else:
            raise AssertionError(f"expected redirect rejection for {target}")
        return requested

    for target in targets:
        assert asyncio.run(_run(target)) == ["https://i.pximg.net/start.jpg"]


def test_stream_url_allows_relative_same_host_redirect_for_non_pximg() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(307, headers={"Location": "/final"}, request=request)
        return httpx.Response(200, content=b"ok", request=request)

    async def _run() -> bytes:
        resp = await stream_url(
            "  https://edge.example.test/start  ",
            transport=httpx.MockTransport(handler),
            cache_control="no-store",
        )
        return b"".join([chunk async for chunk in resp.body_iterator])

    assert asyncio.run(_run()) == b"ok"
    assert requested == ["https://edge.example.test/start", "https://edge.example.test/final"]


def test_stream_url_closes_redirect_responses_and_caps_hops() -> None:
    responses: list[httpx.Response] = []
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        response = httpx.Response(302, headers={"Location": "/loop"}, request=request)
        responses.append(response)
        return response

    async def _run() -> None:
        try:
            await stream_url(
                "https://edge.example.test/start",
                transport=httpx.MockTransport(handler),
                cache_control="no-store",
            )
        except ApiError as exc:
            assert exc.code == ErrorCode.UPSTREAM_STREAM_ERROR
        else:
            raise AssertionError("expected redirect limit rejection")

    asyncio.run(_run())
    assert len(requested) == 4
    assert responses and all(response.is_closed for response in responses)


def test_stream_url_rejects_insecure_initial_urls_before_request() -> None:
    urls = [
        "http://i.pximg.net/start.jpg",
        "https://user:pass@i.pximg.net/start.jpg",
        "https://i.pximg.net:444/start.jpg",
        "https://127.0.0.1/start.jpg",
        "https://i.pximg.net\\@127.0.0.1/start.jpg",
    ]

    async def _run(url: str) -> list[str]:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, content=b"unexpected", request=request)

        try:
            await stream_url(
                url,
                transport=httpx.MockTransport(handler),
                cache_control="no-store",
            )
        except ApiError as exc:
            assert exc.code == ErrorCode.UPSTREAM_STREAM_ERROR
            assert exc.status_code == 502
        else:
            raise AssertionError(f"expected insecure URL rejection for {url}")
        return requested

    for url in urls:
        assert asyncio.run(_run(url)) == []


def test_stream_url_sets_pixiv_referer_header_by_default(monkeypatch) -> None:
    seen_referer: str | None = None
    dummy_stream = _DummyStream([b"x"])

    async def fake_send(self, request: httpx.Request, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal seen_referer
        seen_referer = request.headers.get("Referer")
        return httpx.Response(
            200,
            headers={"Content-Type": "application/octet-stream"},
            stream=dummy_stream,
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send, raising=True)

    async def _run() -> None:
        resp = await stream_url("https://example.test/x.bin", cache_control="no-store")
        async for _ in resp.body_iterator:
            pass
        if resp.background is not None:
            await resp.background()

    asyncio.run(_run())
    assert seen_referer == PIXIV_REFERER


def test_stream_url_closes_on_consumer_cancel(monkeypatch) -> None:
    """Owned transport client is closed on cancel; upstream stream always closed."""
    client_closed = False
    orig_aclose = httpx.AsyncClient.aclose
    blocking_stream: _BlockingStream | None = None
    transport = httpx.MockTransport(lambda _req: httpx.Response(200))

    async def fake_send(self, request: httpx.Request, **kwargs):  # type: ignore[no-untyped-def]
        assert blocking_stream is not None
        return httpx.Response(
            200,
            headers={"Content-Type": "application/octet-stream"},
            stream=blocking_stream,
            request=request,
        )

    async def fake_aclose(self) -> None:  # type: ignore[no-untyped-def]
        nonlocal client_closed
        client_closed = True
        await orig_aclose(self)

    monkeypatch.setattr(httpx.AsyncClient, "send", fake_send, raising=True)
    monkeypatch.setattr(httpx.AsyncClient, "aclose", fake_aclose, raising=True)

    async def _run() -> None:
        nonlocal blocking_stream
        blocking_stream = _BlockingStream()
        # Inject transport so path owns the client (control-plane path must not aclose).
        resp = await stream_url(
            "https://example.test/slow.bin",
            cache_control="no-store",
            transport=transport,
        )
        first_received = asyncio.Event()

        async def _consume() -> None:
            async for _ in resp.body_iterator:
                first_received.set()
                await asyncio.sleep(3600)

        task = asyncio.create_task(_consume())
        await first_received.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        await asyncio.sleep(0)

    asyncio.run(_run())
    assert blocking_stream is not None
    assert blocking_stream.closed is True
    assert client_closed is True
