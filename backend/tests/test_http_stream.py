from __future__ import annotations

import asyncio

import httpx

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
    dummy_stream = _DummyStream([b"abc", b"def"])

    async def fake_send(self, request: httpx.Request, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal sent_stream_flag
        sent_stream_flag = bool(kwargs.get("stream"))
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
    assert body == b"abcdef"
    assert dummy_stream.closed is True


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
    client_closed = False
    orig_aclose = httpx.AsyncClient.aclose
    blocking_stream: _BlockingStream | None = None

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
        resp = await stream_url("https://example.test/slow.bin", cache_control="no-store")
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
