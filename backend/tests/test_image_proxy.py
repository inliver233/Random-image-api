from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker
from app.main import create_app


def test_image_proxy_streams_bytes(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "image_proxy.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()
    image_id: int | None = None

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            img = Image(
                illust_id=123,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/origin.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.5,
            )
            session.add(img)
            await session.commit()
            await session.refresh(img)
            nonlocal image_id
            image_id = img.id

        await app.state.engine.dispose()

    asyncio.run(_seed())
    assert image_id is not None

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("Referer") == "https://www.pixiv.net/"
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"img-bytes")

    app.state.httpx_transport = httpx.MockTransport(handler)

    with TestClient(app) as client:
        resp = client.get(f"/i/{image_id}.jpg", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        assert resp.content == b"img-bytes"
        assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        assert resp.headers["X-Request-Id"] == "req_test"


def test_image_proxy_range_passthrough_returns_206(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "image_proxy_range.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()
    image_id: int | None = None

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            img = Image(
                illust_id=123,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/origin.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.5,
            )
            session.add(img)
            await session.commit()
            await session.refresh(img)
            nonlocal image_id
            image_id = img.id

        await app.state.engine.dispose()

    asyncio.run(_seed())
    assert image_id is not None

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("Referer") == "https://www.pixiv.net/"
        assert req.headers.get("Range") == "bytes=0-2"
        return httpx.Response(
            206,
            headers={
                "Content-Type": "image/jpeg",
                "Content-Length": "3",
                "Accept-Ranges": "bytes",
                "Content-Range": "bytes 0-2/6",
            },
            content=b"abc",
        )

    app.state.httpx_transport = httpx.MockTransport(handler)

    with TestClient(app) as client:
        resp = client.get(
            f"/i/{image_id}.jpg",
            headers={"X-Request-Id": "req_test", "Range": "bytes=0-2"},
        )
        assert resp.status_code == 206
        assert resp.content == b"abc"
        assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        assert resp.headers["Accept-Ranges"] == "bytes"
        assert resp.headers["Content-Range"] == "bytes 0-2/6"
        assert resp.headers["X-Request-Id"] == "req_test"


def test_image_proxy_ext_mismatch_returns_404(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "image_proxy_mismatch.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()
    image_id: int | None = None

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            img = Image(
                illust_id=123,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/origin.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.5,
            )
            session.add(img)
            await session.commit()
            await session.refresh(img)
            nonlocal image_id
            image_id = img.id

        await app.state.engine.dispose()

    asyncio.run(_seed())
    assert image_id is not None

    with TestClient(app) as client:
        resp = client.get(f"/i/{image_id}.png", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 404
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "NOT_FOUND"
        assert body["request_id"] == "req_test"


def test_image_proxy_invalid_ext_returns_400(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "image_proxy_invalid_ext.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                Image(
                    illust_id=123,
                    page_index=0,
                    ext="jpg",
                    original_url="https://example.test/origin.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.5,
                )
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/i/1.exe", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"


def test_image_proxy_upstream_404_marks_failure(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "image_proxy_upstream_404.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()
    image_id: int | None = None

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            img = Image(
                illust_id=123,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/origin.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.5,
            )
            session.add(img)
            await session.commit()
            await session.refresh(img)
            nonlocal image_id
            image_id = img.id

    asyncio.run(_seed())
    assert image_id is not None

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("Referer") == "https://www.pixiv.net/"
        return httpx.Response(404, headers={"Content-Type": "text/plain"}, content=b"not found")

    app.state.httpx_transport = httpx.MockTransport(handler)

    with TestClient(app) as client:
        resp = client.get(f"/i/{image_id}.jpg", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 502
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "UPSTREAM_404"
        assert body["request_id"] == "req_test"

    async def _get_row() -> Image:
        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            row = await session.get(Image, int(image_id))
            assert row is not None
            return row

    row = asyncio.run(_get_row())
    assert int(row.fail_count) == 1
    assert row.last_fail_at is not None
    assert row.last_error_code == "UPSTREAM_404"
