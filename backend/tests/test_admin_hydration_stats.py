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


def test_admin_summary_includes_hydration_missing_counts(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_summary_hydration.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

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

    asyncio.run(_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.get(
            "/admin/api/summary",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"

        hydration = body["counts"]["hydration"]
        assert hydration["enabled_images_total"] == 2
        missing = hydration["missing"]
        assert missing["tags"] == 1
        assert missing["geometry"] == 1
        assert missing["r18"] == 1
        assert missing["ai"] == 1
        assert missing["illust_type"] == 1
        assert missing["user"] == 1
        assert missing["title"] == 1
        assert missing["created_at"] == 1
        assert missing["popularity"] == 1
