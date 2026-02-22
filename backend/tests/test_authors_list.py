from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker
from app.main import create_app


def test_authors_list_search_and_pagination(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "authors_list.db"
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
                        illust_id=100,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/100.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.1,
                        user_id=1,
                        user_name="Alice",
                    ),
                    Image(
                        illust_id=101,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/101.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.2,
                        user_id=1,
                        user_name="Alice",
                    ),
                    Image(
                        illust_id=102,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/102.jpg",
                        proxy_path="/i/3.jpg",
                        random_key=0.3,
                        user_id=2,
                        user_name="Bob",
                    ),
                    Image(
                        illust_id=103,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/103.jpg",
                        proxy_path="/i/4.jpg",
                        random_key=0.4,
                        user_id=None,
                        user_name=None,
                    ),
                ]
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        page1 = client.get("/authors", params={"limit": 1}, headers={"X-Request-Id": "req_test"})
        assert page1.status_code == 200
        body1 = page1.json()
        assert body1["ok"] is True
        assert body1["request_id"] == "req_test"
        assert page1.headers["X-Request-Id"] == "req_test"
        assert [i["user_id"] for i in body1["items"]] == ["1"]
        assert body1["items"][0]["count_images"] == 2
        assert body1["next_cursor"] == "1"

        page2 = client.get(
            "/authors",
            params={"limit": 1, "cursor": body1["next_cursor"]},
            headers={"X-Request-Id": "req_test"},
        )
        assert page2.status_code == 200
        body2 = page2.json()
        assert [i["user_id"] for i in body2["items"]] == ["2"]
        assert body2["next_cursor"] == ""

        search = client.get("/authors", params={"q": "Ali"}, headers={"X-Request-Id": "req_test"})
        assert search.status_code == 200
        body3 = search.json()
        assert [i["user_id"] for i in body3["items"]] == ["1"]


def test_authors_list_invalid_cursor_returns_400(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "authors_list_invalid_cursor.db"
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
        resp = client.get("/authors", params={"cursor": "nope"}, headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"


def test_authors_list_uses_fts_when_available(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "authors_list_fts.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    async def _seed() -> bool:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                Image(
                    illust_id=100,
                    page_index=0,
                    ext="jpg",
                    original_url="https://example.test/100.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.1,
                    user_id=1,
                    user_name="Alice",
                )
            )
            await session.commit()

        await app.state.engine.dispose()

        try:
            con = sqlite3.connect(db_path)
            try:
                con.execute("CREATE VIRTUAL TABLE authors_fts USING fts5(user_name, tokenize='trigram');")
            except sqlite3.OperationalError:
                con.execute("CREATE VIRTUAL TABLE authors_fts USING fts5(user_name);")
            con.execute("INSERT INTO authors_fts(rowid, user_name) VALUES (?,?);", (1, "Alicia"))
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
        resp = client.get("/authors", params={"q": "Alicia"}, headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        body = resp.json()
        assert [i["user_id"] for i in body["items"]] == ["1"]
