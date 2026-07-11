from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker
from app.main import create_app


def test_legacy_single_streams_bytes(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "legacy_single.db"
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

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("Referer") == "https://www.pixiv.net/"
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"img-bytes")

    transport = httpx.MockTransport(handler)
    app.state.httpx_transport = transport
    app.state.httpx_client = httpx.AsyncClient(transport=transport, follow_redirects=True)

    with TestClient(app) as client:
        resp = client.get("/123.jpg", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        assert resp.content == b"img-bytes"
        assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        assert resp.headers["X-Request-Id"] == "req_test"


def test_legacy_single_ext_mismatch_returns_404(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "legacy_single_mismatch.db"
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
        resp = client.get("/123.png", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 404
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "NOT_FOUND"
        assert body["request_id"] == "req_test"


def test_legacy_multi_one_based_page(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "legacy_multi.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add_all(
                [
                    Image(
                        illust_id=123,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/origin_p0.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.5,
                    ),
                    Image(
                        illust_id=123,
                        page_index=1,
                        ext="png",
                        original_url="https://example.test/origin_p1.png",
                        proxy_path="/i/2.png",
                        random_key=0.6,
                    ),
                ]
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("Referer") == "https://www.pixiv.net/"
        if str(req.url) == "https://example.test/origin_p1.png":
            return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"png-bytes")
        return httpx.Response(404, content=b"")

    transport = httpx.MockTransport(handler)
    app.state.httpx_transport = transport
    app.state.httpx_client = httpx.AsyncClient(transport=transport, follow_redirects=True)

    with TestClient(app) as client:
        resp = client.get("/123-2.png", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        assert resp.content == b"png-bytes"
        assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        assert resp.headers["X-Request-Id"] == "req_test"


def test_legacy_single_prefers_image_edge(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "legacy_edge.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("IMAGE_EDGE_ENABLED", "true")
    monkeypatch.setenv("IMAGE_EDGE_SECRET", "edge-secret")
    monkeypatch.setenv("IMAGE_EDGE_BASE_URLS", "https://img.example.com")
    monkeypatch.setenv("IMAGE_EDGE_SIGN_TTL_SECONDS", "3600")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                Image(
                    illust_id=555,
                    page_index=0,
                    ext="jpg",
                    original_url="https://i.pximg.net/img-original/img/2021/02/03/04/05/06/555_p0.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.5,
                )
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/555.jpg", headers={"X-Request-Id": "req_legacy_edge"}, follow_redirects=False)
        assert resp.status_code == 302
        loc = resp.headers.get("location") or ""
        assert loc.startswith("https://img.example.com/u/")
        assert resp.headers.get("x-image-edge") == "1"


def test_legacy_single_force_local_streams(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "legacy_force_local.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("IMAGE_EDGE_ENABLED", "true")
    monkeypatch.setenv("IMAGE_EDGE_SECRET", "edge-secret")
    monkeypatch.setenv("IMAGE_EDGE_BASE_URLS", "https://img.example.com")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                Image(
                    illust_id=556,
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

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"local-bytes")

    transport = httpx.MockTransport(handler)
    app.state.httpx_transport = transport
    app.state.httpx_client = httpx.AsyncClient(transport=transport, follow_redirects=True)

    with TestClient(app) as client:
        resp = client.get("/556.jpg?local=1", headers={"X-Request-Id": "req_legacy_local"})
        assert resp.status_code == 200
        assert resp.content == b"local-bytes"


def test_legacy_multi_invalid_page_returns_400(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "legacy_multi_invalid_page.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        await app.state.engine.dispose()

    asyncio.run(_migrate())

    with TestClient(app) as client:
        resp = client.get("/123-0.jpg", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"
