from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.tags import Tag
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_images_list_missing_filters_and_cursor(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_images_list.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    ids: dict[str, int] = {}

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            img1 = Image(
                illust_id=1,
                page_index=0,
                ext="jpg",
                original_url="https://example.com/1.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
                width=None,
                height=None,
                x_restrict=None,
                ai_type=None,
                user_id=None,
                title=None,
                created_at_pixiv=None,
            )
            img2 = Image(
                illust_id=2,
                page_index=0,
                ext="jpg",
                original_url="https://example.com/2.jpg",
                proxy_path="/i/2.jpg",
                random_key=0.2,
                width=100,
                height=200,
                x_restrict=0,
                ai_type=0,
                illust_type=0,
                user_id=123,
                user_name="u",
                title="t",
                created_at_pixiv="2026-01-01T00:00:00Z",
                bookmark_count=1,
                view_count=2,
                comment_count=3,
            )
            session.add_all([img1, img2])
            await session.flush()

            tag = Tag(name="tag1", translated_name=None)
            session.add(tag)
            await session.flush()
            session.add(ImageTag(image_id=int(img2.id), tag_id=int(tag.id)))

            await session.commit()
            ids["img1"] = int(img1.id)
            ids["img2"] = int(img2.id)

    asyncio.run(_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        r0 = client.get("/admin/api/images", headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"})
        assert r0.status_code == 200
        b0 = r0.json()
        assert b0["ok"] is True
        assert [int(x["id"]) for x in b0["items"]] == [ids["img2"], ids["img1"]]

        r1 = client.get(
            "/admin/api/images",
            params={"missing": "tags"},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert r1.status_code == 200
        b1 = r1.json()
        assert len(b1["items"]) == 1
        assert int(b1["items"][0]["id"]) == ids["img1"]

        r2 = client.get(
            "/admin/api/images",
            params={"missing": "geometry"},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert r2.status_code == 200
        assert int(r2.json()["items"][0]["id"]) == ids["img1"]

        r2b = client.get(
            "/admin/api/images",
            params={"missing": "popularity"},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert r2b.status_code == 200
        b2b = r2b.json()
        assert len(b2b["items"]) == 1
        assert int(b2b["items"][0]["id"]) == ids["img1"]

        r2c = client.get(
            "/admin/api/images",
            params={"missing": "illust_type"},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert r2c.status_code == 200
        b2c = r2c.json()
        assert len(b2c["items"]) == 1
        assert int(b2c["items"][0]["id"]) == ids["img1"]

        r3 = client.get(
            "/admin/api/images",
            params={"limit": 1},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert r3.status_code == 200
        b3 = r3.json()
        assert len(b3["items"]) == 1
        assert b3["next_cursor"] == str(ids["img2"])

        r4 = client.get(
            "/admin/api/images",
            params={"limit": 1, "cursor": b3["next_cursor"]},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert r4.status_code == 200
        b4 = r4.json()
        assert len(b4["items"]) == 1
        assert int(b4["items"][0]["id"]) == ids["img1"]

        bad = client.get(
            "/admin/api/images",
            params={"missing": "nope"},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert bad.status_code == 400
        assert bad.json()["ok"] is False
