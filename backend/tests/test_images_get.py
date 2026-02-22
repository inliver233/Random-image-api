from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.tags import Tag
from app.db.session import create_sessionmaker
from app.main import create_app


def test_images_get_returns_item_and_tags(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "images_get.db"
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
            t1 = Tag(name="b")
            t2 = Tag(name="a")
            session.add_all([img, t1, t2])
            await session.commit()
            await session.refresh(img)
            await session.refresh(t1)
            await session.refresh(t2)
            session.add_all([ImageTag(image_id=int(img.id), tag_id=int(t1.id)), ImageTag(image_id=int(img.id), tag_id=int(t2.id))])
            await session.commit()
            nonlocal image_id
            image_id = int(img.id)

        await app.state.engine.dispose()

    asyncio.run(_seed())
    assert image_id is not None

    with TestClient(app) as client:
        resp = client.get(f"/images/{image_id}", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"
        assert body["item"]["image"]["id"] == str(image_id)
        assert body["item"]["tags"] == ["a", "b"]


def test_images_get_missing_returns_404(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "images_get_missing.db"
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
        resp = client.get("/images/999", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 404
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "NOT_FOUND"
        assert body["request_id"] == "req_test"


def test_images_get_invalid_id_returns_400(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "images_get_invalid.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    with TestClient(app) as client:
        resp = client.get("/images/0", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"

