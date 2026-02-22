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


def test_random_json_includes_tags(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_json_tags.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            tag = Tag(name="cat")
            img = Image(
                illust_id=123,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/origin.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.5,
                x_restrict=0,
            )
            session.add_all([tag, img])
            await session.flush()
            session.add(ImageTag(image_id=img.id, tag_id=tag.id))
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/random?format=json", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["data"]["tags"] == ["cat"]

