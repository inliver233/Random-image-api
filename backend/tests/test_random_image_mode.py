from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker
from app.main import create_app


def test_random_image_streams_bytes(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_image.db"
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
                    x_restrict=0,
                )
            )
            await session.commit()
        await app.state.engine.dispose()

    asyncio.run(_seed())

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("Referer") == "https://www.pixiv.net/"
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"img-bytes")

    app.state.httpx_transport = httpx.MockTransport(handler)

    with TestClient(app) as client:
        resp = client.get("/random", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        assert resp.content == b"img-bytes"
        assert resp.headers["Cache-Control"] == "no-store"
        assert resp.headers["X-Request-Id"] == "req_test"


def test_random_image_redirects_to_proxy_path(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_redirect.db"
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
                x_restrict=0,
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
        resp = client.get(
            "/random?redirect=1",
            headers={"X-Request-Id": "req_test"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == f"/i/{image_id}.jpg"
        assert resp.headers["Cache-Control"] == "no-store"
        assert resp.headers["X-Request-Id"] == "req_test"


def test_random_image_no_match_returns_404(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_image_empty.db"
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
        resp = client.get("/random", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 404
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "NO_MATCH"
        assert body["request_id"] == "req_test"
        hints = body["details"]["hints"]
        assert hints["applied_filters"]["r18"] == 0
        assert isinstance(hints["suggestions"], list)


def test_random_json_returns_shape_and_hides_origin_by_default(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_json.db"
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
                    x_restrict=0,
                )
            )
            await session.commit()
        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/random?format=json", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["code"] == "OK"
        assert body["request_id"] == "req_test"
        assert body["data"]["image"]["id"].isdigit()
        image_id = body["data"]["image"]["id"]
        assert body["data"]["urls"]["proxy"] == f"/i/{image_id}.jpg"
        assert body["data"]["urls"]["origin"] is None
