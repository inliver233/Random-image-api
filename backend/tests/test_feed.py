from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.recent_dedup import clear_recent
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker
from app.main import create_app


def test_feed_returns_batch_simple_items(tmp_path: Path, monkeypatch) -> None:
    clear_recent()
    db_path = tmp_path / "feed_batch.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            for i in range(5):
                session.add(
                    Image(
                        illust_id=100 + i,
                        page_index=0,
                        ext="jpg",
                        original_url=f"https://example.test/{i}.jpg",
                        proxy_path=f"/i/{i + 1}.jpg",
                        random_key=0.1 * (i + 1),
                        x_restrict=0,
                        status=1,
                    )
                )
            await session.commit()
        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get(
            "/feed",
            params={"limit": 3, "strategy": "random"},
            headers={"X-Request-Id": "req_feed"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["code"] == "OK"
        assert body["request_id"] == "req_feed"
        assert body["data"]["requested"] == 3
        assert body["data"]["count"] == 3
        items = body["data"]["items"]
        assert len(items) == 3
        ids = set()
        for it in items:
            assert "image" in it and "urls" in it
            assert it["urls"]["local"].startswith("/i/")
            assert "proxy" in it["urls"]
            # Feed items omit debug payload by design (bandwidth).
            assert "debug" not in it
            ids.add(it["image"]["id"])
        assert len(ids) == 3


def test_feed_limit_validation_and_no_match(tmp_path: Path, monkeypatch) -> None:
    clear_recent()
    db_path = tmp_path / "feed_empty.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        bad = client.get("/feed", params={"limit": 99})
        assert bad.status_code == 400

        empty = client.get("/feed", params={"limit": 2, "min_bookmarks": 999999})
        assert empty.status_code == 404
        err = empty.json()
        assert err.get("ok") is False
        assert err.get("code") == "NO_MATCH"
