from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.random_pick import pick_random_image
from app.db.session import create_sessionmaker
from app.main import create_app


def test_pick_random_image_supports_popularity_and_illust_type_filters(tmp_path: Path) -> None:
    db_path = tmp_path / "random_popularity_filters.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add_all(
                [
                    Image(
                        illust_id=1,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/1.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.1,
                        x_restrict=0,
                        illust_type=0,
                        bookmark_count=5,
                        view_count=10,
                        comment_count=0,
                    ),
                    Image(
                        illust_id=2,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/2.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.2,
                        x_restrict=0,
                        illust_type=1,
                        bookmark_count=100,
                        view_count=1000,
                        comment_count=50,
                    ),
                ]
            )
            await session.commit()

        async with Session() as session:
            picked = await pick_random_image(session, r=0.0, min_bookmarks=50)
            assert picked is not None
            assert int(picked.illust_id) == 2

        async with Session() as session:
            picked = await pick_random_image(session, r=0.0, min_views=500)
            assert picked is not None
            assert int(picked.illust_id) == 2

        async with Session() as session:
            picked = await pick_random_image(session, r=0.0, min_comments=10)
            assert picked is not None
            assert int(picked.illust_id) == 2

        async with Session() as session:
            picked = await pick_random_image(session, r=0.0, illust_type=0)
            assert picked is not None
            assert int(picked.illust_id) == 1

        await engine.dispose()

    asyncio.run(_run())


def test_random_api_supports_layout_adaptive_and_new_filters(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_api_filters.db"
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
                        illust_id=1,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/p.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.1,
                        x_restrict=0,
                        orientation=1,
                        width=1000,
                        height=1000,
                        illust_type=0,
                        bookmark_count=10,
                        view_count=100,
                        comment_count=1,
                    ),
                    Image(
                        illust_id=2,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/l.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.2,
                        x_restrict=0,
                        orientation=2,
                        width=1000,
                        height=1000,
                        illust_type=1,
                        bookmark_count=100,
                        view_count=1000,
                        comment_count=50,
                    ),
                ]
            )
            await session.commit()
        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        r_layout = client.get("/random", params={"format": "json", "layout": "portrait", "attempts": 1})
        assert r_layout.status_code == 200
        assert r_layout.json()["data"]["image"]["illust_id"] == "1"

        r_min_bookmarks = client.get("/random", params={"format": "json", "min_bookmarks": 50, "attempts": 1})
        assert r_min_bookmarks.status_code == 200
        assert r_min_bookmarks.json()["data"]["image"]["illust_id"] == "2"

        r_illust_type = client.get("/random", params={"format": "json", "illust_type": "manga", "attempts": 1})
        assert r_illust_type.status_code == 200
        assert r_illust_type.json()["data"]["image"]["illust_id"] == "2"

        headers_mobile = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}
        r_adaptive = client.get("/random", params={"format": "json", "adaptive": 1, "attempts": 1}, headers=headers_mobile)
        assert r_adaptive.status_code == 200
        assert r_adaptive.json()["data"]["image"]["illust_id"] == "1"

        # adaptive 不覆盖用户显式传入的 orientation
        r_adaptive_keep = client.get(
            "/random",
            params={"format": "json", "adaptive": 1, "orientation": "landscape", "attempts": 1},
            headers=headers_mobile,
        )
        assert r_adaptive_keep.status_code == 200
        assert r_adaptive_keep.json()["data"]["image"]["illust_id"] == "2"

