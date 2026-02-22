from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.db.models.images import Image
from app.db.models.runtime_settings import RuntimeSetting
from app.db.session import create_sessionmaker
from app.main import create_app


def test_random_uses_runtime_defaults_for_strategy_and_quality_samples(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_runtime_defaults.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()

    seed = "seed_runtime_defaults_001"
    samples = 3

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                RuntimeSetting(
                    key="random.defaults",
                    value_json=json.dumps(
                        {"strategy": "quality", "quality_samples": samples},
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                    description=None,
                    updated_by=None,
                )
            )
            session.add_all(
                [
                    Image(
                        illust_id=1,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/1.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.10,
                        x_restrict=0,
                        ai_type=0,
                        width=1200,
                        height=800,
                        bookmark_count=10,
                        view_count=100,
                        comment_count=1,
                    ),
                    Image(
                        illust_id=2,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/2.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.30,
                        x_restrict=0,
                        ai_type=0,
                        width=800,
                        height=800,
                        bookmark_count=500,
                        view_count=20000,
                        comment_count=50,
                    ),
                    Image(
                        illust_id=3,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/3.jpg",
                        proxy_path="/i/3.jpg",
                        random_key=0.60,
                        x_restrict=0,
                        ai_type=0,
                        width=3000,
                        height=2000,
                        bookmark_count=200,
                        view_count=4000,
                        comment_count=10,
                    ),
                    Image(
                        illust_id=4,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/4.jpg",
                        proxy_path="/i/4.jpg",
                        random_key=0.85,
                        x_restrict=0,
                        ai_type=0,
                        width=900,
                        height=1600,
                        bookmark_count=30,
                        view_count=300,
                        comment_count=2,
                    ),
                ]
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp1 = client.get("/random", params={"format": "json", "attempts": 1, "seed": seed})
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert body1["ok"] is True
        assert body1["data"]["debug"]["strategy"] == "quality"
        assert body1["data"]["debug"]["strategy_source"] == "runtime"
        assert body1["data"]["debug"]["quality_samples"] == samples
        assert body1["data"]["debug"]["quality_samples_source"] == "runtime"
        id1 = int(body1["data"]["image"]["id"])

        resp2 = client.get(
            "/random",
            params={
                "format": "json",
                "attempts": 1,
                "seed": seed,
                "strategy": "quality",
                "quality_samples": samples,
            },
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["ok"] is True
        assert int(body2["data"]["image"]["id"]) == id1
        assert body2["data"]["debug"]["strategy_source"] == "query"
        assert body2["data"]["debug"]["quality_samples_source"] == "query"

