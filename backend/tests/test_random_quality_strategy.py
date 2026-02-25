from __future__ import annotations

import asyncio
import random
import math
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.public.random import _quality_score  # noqa: PLC2701
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.random_pick import pick_random_image
from app.db.session import create_sessionmaker
from app.main import create_app


def test_random_quality_strategy_picks_weighted_by_score(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_quality_strategy.db"
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

    seed = "seed_test_001"
    samples = 3

    async def _compute_expected_quality_pick() -> int:
        rng = random.Random(seed)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            start = float(rng.random())
            ordered = (await session.execute(select(Image).order_by(Image.random_key.asc()))).scalars().all()
            assert ordered
            ordered2 = sorted(ordered, key=lambda x: float(x.random_key))
            start_idx = 0
            for i, img in enumerate(ordered2):
                if float(img.random_key) >= start:
                    start_idx = i
                    break
            take = min(int(samples), len(ordered2))
            picked_imgs = [ordered2[(start_idx + i) % len(ordered2)] for i in range(take)]
            candidates: list[tuple[int, float]] = [(int(img.id), float(_quality_score(img))) for img in picked_imgs]

        assert candidates
        max_logit = max(s for _id, s in candidates)
        weights = [math.exp(float(s) - float(max_logit)) for _id, s in candidates]
        total = float(sum(weights))
        assert total > 0

        r = float(rng.random()) * total
        for (img_id, _s), w in zip(candidates, weights, strict=True):
            r -= float(w)
            if r <= 0:
                return int(img_id)
        return int(candidates[-1][0])

    expected_quality_id = asyncio.run(_compute_expected_quality_pick())

    with TestClient(app) as client:
        resp = client.get(
            "/random",
            params={
                "format": "json",
                "attempts": 1,
                "seed": seed,
                "strategy": "quality",
                "quality_samples": samples,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert int(body["data"]["image"]["id"]) == expected_quality_id
        assert body["data"]["debug"]["picked_by"] == "quality_weighted"
        assert body["data"]["debug"]["quality_samples"] == samples
        assert isinstance(body["data"]["debug"]["quality_score"], float)


def test_random_quality_strategy_supports_rec_query_overrides(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_quality_rec_overrides.db"
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
                        illust_id=11,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/11.jpg",
                        proxy_path="/i/11.jpg",
                        random_key=0.10,
                        x_restrict=0,
                        ai_type=0,
                        illust_type=0,
                        width=1000,
                        height=1000,
                        bookmark_count=100,
                        view_count=100,
                        comment_count=0,
                    ),
                    Image(
                        illust_id=22,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/22.jpg",
                        proxy_path="/i/22.jpg",
                        random_key=0.20,
                        x_restrict=0,
                        ai_type=0,
                        illust_type=0,
                        width=1000,
                        height=1000,
                        bookmark_count=1,
                        view_count=100000,
                        comment_count=0,
                    ),
                ]
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get(
            "/random",
            params={
                "format": "json",
                "attempts": 1,
                "seed": "seed_rec_override_001",
                "strategy": "quality",
                "quality_samples": 2,
                "rec_pick_mode": "best",
                "rec_w_bookmark": 0,
                "rec_w_view": 2,
                "rec_w_comment": 0,
                "rec_w_pixels": 0,
                "rec_w_bookmark_rate": 0,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        # With bookmark weight disabled and view weight boosted, the high-view image should win.
        assert int(body["data"]["image"]["illust_id"]) == 22


def test_random_strategy_random_key_matches_pick_random_image(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_strategy_random_key.db"
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
                        original_url="https://example.test/a.jpg",
                        proxy_path="/i/10.jpg",
                        random_key=0.20,
                        x_restrict=0,
                        ai_type=0,
                    ),
                    Image(
                        illust_id=11,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/b.jpg",
                        proxy_path="/i/11.jpg",
                        random_key=0.80,
                        x_restrict=0,
                        ai_type=0,
                    ),
                ]
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    seed = "seed_test_002"
    rng = random.Random(seed)

    async def _compute_expected() -> int:
        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            img = await pick_random_image(session, r=rng.random())
            assert img is not None
            return int(img.id)

    expected_id = asyncio.run(_compute_expected())

    with TestClient(app) as client:
        resp = client.get("/random", params={"format": "simple_json", "attempts": 1, "seed": seed, "strategy": "random"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert int(body["data"]["image"]["id"]) == expected_id
        assert body["data"]["debug"]["picked_by"] == "random_key"


def test_random_quality_samples_allows_1000(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "random_quality_samples_1000.db"
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
                        proxy_path="/i/100.jpg",
                        random_key=0.20,
                        x_restrict=0,
                        ai_type=0,
                        width=1200,
                        height=800,
                        bookmark_count=10,
                        view_count=100,
                        comment_count=1,
                    ),
                    Image(
                        illust_id=101,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/101.jpg",
                        proxy_path="/i/101.jpg",
                        random_key=0.80,
                        x_restrict=0,
                        ai_type=0,
                        width=800,
                        height=800,
                        bookmark_count=500,
                        view_count=20000,
                        comment_count=50,
                    ),
                ]
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get(
            "/random",
            params={
                "format": "json",
                "attempts": 1,
                "seed": "seed_quality_1000",
                "strategy": "quality",
                "quality_samples": 1000,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["data"]["debug"]["quality_samples"] == 1000
