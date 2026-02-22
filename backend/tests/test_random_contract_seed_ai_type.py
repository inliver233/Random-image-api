from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker
from app.main import create_app


def test_random_seed_is_deterministic_for_json(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_seed.db"
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
                        original_url="https://example.test/1.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.2,
                        x_restrict=0,
                        ai_type=0,
                    ),
                    Image(
                        illust_id=2,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/2.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.8,
                        x_restrict=0,
                        ai_type=0,
                    ),
                ]
            )
            await session.commit()
        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        r1 = client.get("/random", params={"format": "json", "seed": "abc", "attempts": 1})
        r2 = client.get("/random", params={"format": "json", "seed": "abc", "attempts": 1})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["data"]["image"]["id"] == r2.json()["data"]["image"]["id"]


def test_random_ai_type_filter_and_simple_json_shape(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_ai_type.db"
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
                        illust_id=10,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/a0.jpg",
                        proxy_path="/i/10.jpg",
                        random_key=0.3,
                        x_restrict=0,
                        ai_type=0,
                    ),
                    Image(
                        illust_id=11,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/a1.jpg",
                        proxy_path="/i/11.jpg",
                        random_key=0.6,
                        x_restrict=0,
                        ai_type=1,
                    ),
                ]
            )
            await session.commit()
        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp_ai1 = client.get("/random", params={"format": "json", "ai_type": "1", "attempts": 1})
        assert resp_ai1.status_code == 200
        assert resp_ai1.json()["data"]["image"]["ai_type"] == 1

        resp_ai0 = client.get("/random", params={"format": "json", "ai_type": "0", "attempts": 1})
        assert resp_ai0.status_code == 200
        assert resp_ai0.json()["data"]["image"]["ai_type"] == 0

        resp_simple = client.get("/random", params={"format": "simple_json", "attempts": 1})
        assert resp_simple.status_code == 200
        body = resp_simple.json()
        assert body["ok"] is True
        assert body["code"] == "OK"
        assert "tags" not in body.get("data", {})

        resp_bad = client.get("/random", params={"format": "json", "ai_type": "bad"})
        assert resp_bad.status_code == 400
        bad_body = resp_bad.json()
        assert bad_body["ok"] is False
        assert bad_body["code"] == "BAD_REQUEST"

