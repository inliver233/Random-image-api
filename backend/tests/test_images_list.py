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


def test_images_list_pagination_and_filters(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "images_list.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    ids: dict[str, int] = {}

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
                x_restrict=0,
                ai_type=0,
                user_id=1,
                user_name="u1",
            )
            img2 = Image(
                illust_id=101,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/101.jpg",
                proxy_path="/i/2.jpg",
                random_key=0.2,
                x_restrict=1,
                ai_type=1,
                user_id=2,
                user_name="u2",
            )
            img3 = Image(
                illust_id=102,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/102.jpg",
                proxy_path="/i/3.jpg",
                random_key=0.3,
                x_restrict=0,
                ai_type=1,
                user_id=3,
                user_name="u3",
            )
            session.add_all([img1, img2, img3])

            t_cat = Tag(name="cat")
            t_dog = Tag(name="dog")
            session.add_all([t_cat, t_dog])

            await session.commit()
            await session.refresh(img1)
            await session.refresh(img2)
            await session.refresh(img3)
            await session.refresh(t_cat)

            session.add(ImageTag(image_id=int(img3.id), tag_id=int(t_cat.id)))
            await session.commit()

            ids["img1"] = int(img1.id)
            ids["img2"] = int(img2.id)
            ids["img3"] = int(img3.id)

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        page1 = client.get("/images", params={"limit": 2, "r18": 2}, headers={"X-Request-Id": "req_test"})
        assert page1.status_code == 200
        body1 = page1.json()
        assert body1["ok"] is True
        assert body1["request_id"] == "req_test"
        assert len(body1["items"]) == 2
        assert body1["next_cursor"].isdigit()

        # Ordered by id DESC
        assert int(body1["items"][0]["id"]) == ids["img3"]
        assert int(body1["items"][1]["id"]) == ids["img2"]

        page2 = client.get(
            "/images",
            params={"limit": 2, "cursor": body1["next_cursor"], "r18": 2},
        )
        assert page2.status_code == 200
        body2 = page2.json()
        assert len(body2["items"]) == 1
        assert int(body2["items"][0]["id"]) == ids["img1"]
        assert body2["next_cursor"] == ""

        only_ai0 = client.get("/images", params={"r18": 2, "ai_type": 0})
        assert only_ai0.status_code == 200
        items_ai0 = only_ai0.json()["items"]
        assert len(items_ai0) == 1
        assert items_ai0[0]["ai_type"] == 0

        only_cat = client.get("/images", params={"r18": 2, "included_tags": "cat"})
        assert only_cat.status_code == 200
        items_cat = only_cat.json()["items"]
        assert len(items_cat) == 1
        assert int(items_cat[0]["id"]) == ids["img3"]


def test_images_list_invalid_limit_returns_400(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "images_list_invalid_limit.db"
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
        resp = client.get("/images", params={"limit": 0}, headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"

