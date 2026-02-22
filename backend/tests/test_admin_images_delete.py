from __future__ import annotations

import asyncio
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.tags import Tag
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_images_delete_bulk_and_clear(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_images_delete.db"
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
            )
            img2 = Image(
                illust_id=2,
                page_index=0,
                ext="jpg",
                original_url="https://example.com/2.jpg",
                proxy_path="/i/2.jpg",
                random_key=0.2,
            )
            session.add_all([img1, img2])
            await session.flush()

            tag = Tag(name="tag1", translated_name=None)
            session.add(tag)
            await session.flush()
            session.add_all([ImageTag(image_id=int(img1.id), tag_id=int(tag.id)), ImageTag(image_id=int(img2.id), tag_id=int(tag.id))])

            await session.commit()
            ids["img1"] = int(img1.id)
            ids["img2"] = int(img2.id)
            ids["tag"] = int(tag.id)

    asyncio.run(_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    headers = {"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"}

    with TestClient(app) as client:
        # single delete
        r0 = client.delete(f"/admin/api/images/{ids['img2']}", headers=headers)
        assert r0.status_code == 200
        assert r0.json()["ok"] is True
        assert r0.json()["image_id"] == str(ids["img2"])

        # verify image removed, other remains
        r0b = client.get("/admin/api/images", headers=headers)
        assert r0b.status_code == 200
        assert [int(x["id"]) for x in r0b.json()["items"]] == [ids["img1"]]

        # bulk delete ignores duplicates and reports missing
        r1 = client.post(
            "/admin/api/images/bulk-delete",
            headers=headers,
            json={"image_ids": [ids["img1"], ids["img1"], 999999]},
        )
        assert r1.status_code == 200
        b1 = r1.json()
        assert b1["ok"] is True
        assert int(b1["requested"]) == 2
        assert int(b1["deleted"]) == 1
        assert int(b1["missing"]) == 1

        r1b = client.get("/admin/api/images", headers=headers)
        assert r1b.status_code == 200
        assert r1b.json()["items"] == []

        # clear removes tags by default
        r2 = client.post("/admin/api/images/clear", headers=headers, json={"confirm": True})
        assert r2.status_code == 200
        assert r2.json()["ok"] is True

        # verify tables are empty
        Session = create_sessionmaker(app.state.engine)

        async def _check() -> tuple[int, int, int]:
            async with Session() as session:
                c_images = int((await session.execute(sa.select(sa.func.count()).select_from(Image))).scalar_one())
                c_links = int((await session.execute(sa.select(sa.func.count()).select_from(ImageTag))).scalar_one())
                c_tags = int((await session.execute(sa.select(sa.func.count()).select_from(Tag))).scalar_one())
                return c_images, c_links, c_tags

        c_images, c_links, c_tags = asyncio.run(_check())
        assert c_images == 0
        assert c_links == 0
        assert c_tags == 0

