from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.db.models.base import Base
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.tags import Tag
from app.db.session import create_sessionmaker
from app.main import create_app


def test_tags_list_search_and_pagination(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "tags_list.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            img1 = Image(
                illust_id=100,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/100.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
            )
            img2 = Image(
                illust_id=101,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/101.jpg",
                proxy_path="/i/2.jpg",
                random_key=0.2,
            )
            session.add_all([img1, img2])

            t_cat = Tag(name="cat")
            t_car = Tag(name="car")
            t_dog = Tag(name="dog")
            session.add_all([t_cat, t_car, t_dog])

            await session.commit()
            await session.refresh(img1)
            await session.refresh(img2)
            await session.refresh(t_cat)
            await session.refresh(t_car)
            await session.refresh(t_dog)

            session.add_all(
                [
                    ImageTag(image_id=int(img1.id), tag_id=int(t_car.id)),
                    ImageTag(image_id=int(img1.id), tag_id=int(t_cat.id)),
                    ImageTag(image_id=int(img2.id), tag_id=int(t_cat.id)),
                    ImageTag(image_id=int(img2.id), tag_id=int(t_dog.id)),
                ]
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        page1 = client.get("/tags", params={"limit": 2}, headers={"X-Request-Id": "req_test"})
        assert page1.status_code == 200
        body1 = page1.json()
        assert body1["ok"] is True
        assert body1["request_id"] == "req_test"
        assert page1.headers["X-Request-Id"] == "req_test"
        assert [i["name"] for i in body1["items"]] == ["car", "cat"]
        assert body1["items"][1]["count_images"] == 2
        assert body1["next_cursor"] == "cat"

        page2 = client.get(
            "/tags",
            params={"limit": 2, "cursor": body1["next_cursor"]},
            headers={"X-Request-Id": "req_test"},
        )
        assert page2.status_code == 200
        body2 = page2.json()
        assert [i["name"] for i in body2["items"]] == ["dog"]
        assert body2["next_cursor"] == ""

        search = client.get("/tags", params={"q": "ca"}, headers={"X-Request-Id": "req_test"})
        assert search.status_code == 200
        names = [i["name"] for i in search.json()["items"]]
        assert names == ["car", "cat"]


def test_tags_list_invalid_limit_returns_400(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "tags_list_invalid_limit.db"
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
        resp = client.get("/tags", params={"limit": 0}, headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"


def test_tags_list_uses_fts_when_available(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "tags_list_fts.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    async def _seed() -> bool:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        tag_id: int
        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            img = Image(
                illust_id=100,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/100.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
            )
            session.add(img)

            t_cat = Tag(name="cat", translated_name=None)
            session.add(t_cat)

            await session.commit()
            await session.refresh(img)
            await session.refresh(t_cat)

            tag_id = int(t_cat.id)
            session.add(ImageTag(image_id=int(img.id), tag_id=int(t_cat.id)))
            await session.commit()

        await app.state.engine.dispose()

        try:
            con = sqlite3.connect(db_path)
            try:
                con.execute("CREATE VIRTUAL TABLE tags_fts USING fts5(name, translated_name, tokenize='trigram');")
            except sqlite3.OperationalError:
                con.execute("CREATE VIRTUAL TABLE tags_fts USING fts5(name, translated_name);")
            con.execute(
                "INSERT INTO tags_fts(rowid, name, translated_name) VALUES (?,?,?);",
                (tag_id, "neko", ""),
            )
            con.commit()
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            try:
                con.close()
            except Exception:
                pass

    fts_ok = asyncio.run(_seed())
    if not fts_ok:
        pytest.skip("fts5 not available in sqlite3 build")

    with TestClient(app) as client:
        resp = client.get("/tags", params={"q": "neko"}, headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        names = [i["name"] for i in resp.json()["items"]]
        assert names == ["cat"]
