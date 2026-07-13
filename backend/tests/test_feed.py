from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.recent_dedup import clear_recent
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker
from app.main import create_app


class _CountingPick:
    def __init__(self, delegate) -> None:  # type: ignore[no-untyped-def]
        self._delegate = delegate
        self.backend = delegate.backend
        self.calls = {"many": 0, "one": 0}

    async def pick_many(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls["many"] += 1
        return await self._delegate.pick_many(*args, **kwargs)

    async def pick_one(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls["one"] += 1
        return await self._delegate.pick_one(*args, **kwargs)

    async def count_candidates(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await self._delegate.count_candidates(*args, **kwargs)


def test_feed_returns_batch_simple_items(tmp_path: Path, monkeypatch) -> None:
    clear_recent()
    db_path = tmp_path / "feed_batch.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()
    counting_pick = _CountingPick(app.state.random_pick)
    app.state.random_pick = counting_pick

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
        # Envelope debug is opt-in only (?debug=1).
        assert "debug" not in body["data"]

    assert counting_pick.calls == {"many": 1, "one": 0}


def test_feed_limit_32_uses_one_python_batch_call(tmp_path: Path, monkeypatch) -> None:
    clear_recent()
    db_path = tmp_path / "feed_limit_32.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()
    counting_pick = _CountingPick(app.state.random_pick)
    app.state.random_pick = counting_pick

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            for i in range(40):
                session.add(
                    Image(
                        illust_id=1000 + i,
                        page_index=0,
                        ext="jpg",
                        original_url=f"https://example.test/limit32/{i}.jpg",
                        proxy_path=f"/i/{i + 1}.jpg",
                        random_key=(i + 1) / 100.0,
                        x_restrict=0,
                        status=1,
                    )
                )
            await session.commit()
        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/feed", params={"limit": 32, "strategy": "random"})
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) == 32
        assert len({int(item["image"]["id"]) for item in items}) == 32

    assert counting_pick.calls == {"many": 1, "one": 0}


def test_feed_quality_batch_selects_without_requery(tmp_path: Path, monkeypatch) -> None:
    clear_recent()
    db_path = tmp_path / "feed_quality_batch.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)

    app = create_app()
    counting_pick = _CountingPick(app.state.random_pick)
    app.state.random_pick = counting_pick

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            for i in range(6):
                session.add(
                    Image(
                        illust_id=2000 + i,
                        page_index=0,
                        ext="jpg",
                        original_url=f"https://example.test/quality/{i}.jpg",
                        proxy_path=f"/i/{i + 1}.jpg",
                        random_key=(i + 1) / 10.0,
                        x_restrict=0,
                        ai_type=0,
                        illust_type=0,
                        status=1,
                        bookmark_count=10 * (i + 1),
                        view_count=100 * (i + 1),
                    )
                )
            await session.commit()
        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get(
            "/feed",
            params={"limit": 4, "strategy": "quality", "rec_pick_mode": "best"},
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) == 4
        assert len({int(item["image"]["id"]) for item in items}) == 4

    assert counting_pick.calls == {"many": 1, "one": 0}


def test_feed_debug_envelope_engine_status(tmp_path: Path, monkeypatch) -> None:
    """?debug=1 attaches dual-run batch honesty on the feed envelope (not per item)."""
    clear_recent()
    db_path = tmp_path / "feed_debug.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("RANDOM_ENGINE_ENABLED", "true")
    monkeypatch.setenv("RANDOM_ENGINE_BASE_URL", "http://engine.test")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            for i in range(3):
                session.add(
                    Image(
                        illust_id=300 + i,
                        page_index=0,
                        ext="jpg",
                        original_url=f"https://example.test/dbg/{i}.jpg",
                        proxy_path=f"/i/{i + 1}.jpg",
                        random_key=0.15 * (i + 1),
                        x_restrict=0,
                        status=1,
                    )
                )
            await session.commit()
        await app.state.engine.dispose()

    asyncio.run(_seed())

    async def _fake_try_many(**_kwargs):  # type: ignore[no-untyped-def]
        return [], {"engine_status": "skipped_circuit", "engine": True, "picked_by": "python", "batch": True}

    monkeypatch.setattr(
        "app.core.random_engine_pick.try_pick_many_via_engine",
        _fake_try_many,
    )
    monkeypatch.setattr(
        "app.core.random_engine_client.should_route_pick_to_engine",
        lambda _s: True,
    )
    monkeypatch.setattr(
        "app.core.random_engine_client.random_engine_base_url",
        lambda _s: "http://engine.test",
    )
    # Force circuit-open path through try_engine_batch (local snapshot, no outbound).
    monkeypatch.setattr(
        "app.core.random_engine_client.engine_circuit_allow",
        lambda: False,
    )

    with TestClient(app) as client:
        resp = client.get(
            "/feed",
            params={"limit": 2, "strategy": "random", "debug": "1"},
            headers={"X-Request-Id": "req_feed_debug"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_feed_debug"
        dbg = body["data"]["debug"]
        assert dbg["batch"] is True
        assert dbg["engine_status"] == "skipped_circuit"
        assert dbg["batch_count"] == 0
        assert dbg["topup_count"] == 2
        assert dbg["topup_skip_engine"] is True
        # Items remain lean even when envelope debug is on.
        for it in body["data"]["items"]:
            assert "debug" not in it


def test_feed_topup_skips_engine_after_batch(tmp_path: Path, monkeypatch) -> None:
    """After try_engine_batch, per-item top-up must pass skip_engine=True (no N× dual-run)."""
    clear_recent()
    db_path = tmp_path / "feed_skip_engine.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("RANDOM_ENGINE_ENABLED", "true")
    monkeypatch.setenv("RANDOM_ENGINE_BASE_URL", "http://engine.test")

    app = create_app()
    engine_pick_calls: list[str] = []
    counting_pick = _CountingPick(app.state.random_pick)
    app.state.random_pick = counting_pick

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            for i in range(4):
                session.add(
                    Image(
                        illust_id=200 + i,
                        page_index=0,
                        ext="jpg",
                        original_url=f"https://example.test/topup/{i}.jpg",
                        proxy_path=f"/i/{i + 1}.jpg",
                        random_key=0.2 * (i + 1),
                        x_restrict=0,
                        status=1,
                    )
                )
            await session.commit()
        await app.state.engine.dispose()

    asyncio.run(_seed())

    async def _fake_try_many(**kwargs):  # type: ignore[no-untyped-def]
        engine_pick_calls.append("batch")
        # Partial batch forces Python top-up, which must exclude the Engine row.
        image = await kwargs["session"].get(Image, 1)
        assert image is not None
        return [image], {"engine_status": "ok", "engine": True}

    monkeypatch.setattr(
        "app.core.random_engine_pick.try_pick_many_via_engine",
        _fake_try_many,
    )
    # Ensure dual-run routing would otherwise hit engine on non-skip picks.
    monkeypatch.setattr(
        "app.core.random_engine_client.should_route_pick_to_engine",
        lambda _s: True,
    )
    monkeypatch.setattr(
        "app.core.random_engine_client.random_engine_base_url",
        lambda _s: "http://engine.test",
    )

    with TestClient(app) as client:
        resp = client.get("/feed", params={"limit": 3, "strategy": "random"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["count"] == 3
        assert len({int(item["image"]["id"]) for item in body["data"]["items"]}) == 3

    # One Engine batch attempt and one Python DB batch; no per-item pick query.
    assert engine_pick_calls == ["batch"]
    assert counting_pick.calls == {"many": 1, "one": 0}


def test_feed_traffic_miss_one_skipped_metric_and_sticky_topup(tmp_path: Path, monkeypatch) -> None:
    """Partial TRAFFIC_PERCENT: one skipped_traffic for batch, top-up sticky-skips dual-run."""
    clear_recent()
    db_path = tmp_path / "feed_traffic.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("RANDOM_ENGINE_ENABLED", "true")
    monkeypatch.setenv("RANDOM_ENGINE_BASE_URL", "http://engine.test")
    monkeypatch.setenv("RANDOM_ENGINE_TRAFFIC_PERCENT", "0")

    app = create_app()
    observed: list[str] = []
    counting_pick = _CountingPick(app.state.random_pick)
    app.state.random_pick = counting_pick

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            for i in range(4):
                session.add(
                    Image(
                        illust_id=400 + i,
                        page_index=0,
                        ext="jpg",
                        original_url=f"https://example.test/traffic/{i}.jpg",
                        proxy_path=f"/i/{i + 1}.jpg",
                        # ck_images_random_key: must be in [0, 1)
                        random_key=0.15 * (i + 1),
                        x_restrict=0,
                        status=1,
                    )
                )
            await session.commit()
        await app.state.engine.dispose()

    asyncio.run(_seed())

    def _observe(*, status: str, **_k):  # type: ignore[no-untyped-def]
        observed.append(str(status))

    # try_engine_batch imports observe from metrics at call time; pick path uses engine_pick binding.
    monkeypatch.setattr("app.core.metrics.observe_random_engine_pick", _observe)
    monkeypatch.setattr("app.core.random_engine_pick.observe_random_engine_pick", _observe)
    monkeypatch.setattr(
        "app.core.random_engine_client.should_route_pick_to_engine",
        lambda _s: False,
    )
    monkeypatch.setattr(
        "app.core.random_engine_client.random_engine_base_url",
        lambda _s: "http://engine.test",
    )

    with TestClient(app) as client:
        resp = client.get(
            "/feed",
            params={"limit": 3, "strategy": "random", "debug": "1"},
            headers={"X-Request-Id": "req_feed_traffic"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["count"] == 3
        dbg = body["data"]["debug"]
        assert dbg["engine_status"] == "skipped_traffic"
        assert dbg["topup_skip_engine"] is True
        assert dbg["batch_count"] == 0
        assert dbg["topup_count"] == 3

    assert observed.count("skipped_traffic") == 1, observed
    assert "skipped_sticky" not in observed
    assert counting_pick.calls == {"many": 1, "one": 0}


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
