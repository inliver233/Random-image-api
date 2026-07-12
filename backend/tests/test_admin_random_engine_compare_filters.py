from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.main import create_app


class _FakePick:
    backend = "fake"

    def __init__(self, count: int = 0) -> None:
        self.count = int(count)

    async def pick_one(self, session: Any, **kwargs: Any) -> None:
        _ = session, kwargs
        return None

    async def pick_many(self, session: Any, **kwargs: Any) -> list[Any]:
        _ = session, kwargs
        return []

    async def count_candidates(self, session: Any, **kwargs: Any) -> int:
        _ = session, kwargs
        return int(self.count)


def _bootstrap_app(tmp_path: Path, monkeypatch, *, name: str, engine_url: str | None = None):
    db_path = tmp_path / f"{name}.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    if engine_url is None:
        monkeypatch.delenv("RANDOM_ENGINE_URL", raising=False)
        monkeypatch.setenv("RANDOM_ENGINE_URL", "")
    else:
        monkeypatch.setenv("RANDOM_ENGINE_URL", engine_url)

    app = create_app()
    # Force settings in case process env leaked a non-empty URL.
    if engine_url is None:
        app.state.settings = replace(app.state.settings, random_engine_url="", random_engine_enabled=False)
    else:
        app.state.settings = replace(
            app.state.settings,
            random_engine_url=str(engine_url).rstrip("/"),
            random_engine_enabled=True,
        )

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_seed())
    return app


def _login(client: TestClient) -> str:
    return client.post(
        "/admin/api/login",
        headers={"X-Request-Id": "req_test"},
        json={"username": "admin", "password": "pass_test"},
    ).json()["token"]


def test_compare_filters_requires_engine_url(tmp_path: Path, monkeypatch) -> None:
    app = _bootstrap_app(tmp_path, monkeypatch, name="compare_filters_no_url", engine_url=None)

    with TestClient(app) as client:
        token = _login(client)
        resp = client.post(
            "/admin/api/maintenance/random-engine/compare-filters",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"r18": 0},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body.get("ok") is False
        assert body.get("code") == "BAD_REQUEST"
        assert "RANDOM_ENGINE_URL" in str(body.get("message") or "")


def test_compare_filters_match_and_delta(tmp_path: Path, monkeypatch) -> None:
    app = _bootstrap_app(
        tmp_path,
        monkeypatch,
        name="compare_filters_match",
        engine_url="http://127.0.0.1:18091",
    )
    app.state.random_pick = _FakePick(count=42)

    async def _fake_engine_filter_count(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"filtered": 42, "index_size": 100, "revision": "rev-test"}

    async def _fake_engine_pick(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"code": "OK", "items": [{"id": 7}]}

    class _FakeCatalog:
        backend = "fake"

        async def get_image_by_id(self, session: Any, *, image_id: int) -> Any:
            _ = session
            assert image_id == 7
            return SimpleNamespace(
                id=7,
                bookmark_count=10,
                view_count=100,
                comment_count=1,
                width=100,
                height=100,
            )

    monkeypatch.setattr("app.api.admin.maintenance.engine_filter_count", _fake_engine_filter_count)
    monkeypatch.setattr("app.api.admin.maintenance.engine_pick", _fake_engine_pick)
    app.state.catalog_store = _FakeCatalog()

    with TestClient(app) as client:
        token = _login(client)
        resp = client.post(
            "/admin/api/maintenance/random-engine/compare-filters",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"r18": 0, "min_bookmarks": 0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["cardinality_match"] is True
        assert body["match"] is True
        assert body["python_filtered"] == 42
        assert body["engine_filtered"] == 42
        assert body["delta"] == 0
        assert body["engine_index_size"] == 100
        assert body["engine_revision"] == "rev-test"
        assert isinstance(body.get("filters"), dict)
        assert body["pick_probe"]["ok"] is True
        assert body["pick_probe"]["engine_image_id"] == 7
        assert body["pick_probe"]["in_catalog"] is True


def test_compare_filters_reports_delta(tmp_path: Path, monkeypatch) -> None:
    app = _bootstrap_app(
        tmp_path,
        monkeypatch,
        name="compare_filters_delta",
        engine_url="http://127.0.0.1:18091",
    )
    app.state.random_pick = _FakePick(count=50)

    async def _fake_engine_filter_count(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"filtered": 40, "index_size": 80, "revision": "rev-delta"}

    async def _fake_engine_pick(*_a: Any, **_k: Any) -> dict[str, Any]:
        # Probe succeeds but cardinality mismatch must still fail overall match.
        return {"code": "OK", "items": [{"id": 1}]}

    class _FakeCatalog:
        backend = "fake"

        async def get_image_by_id(self, session: Any, *, image_id: int) -> Any:
            _ = session, image_id
            return SimpleNamespace(
                id=1,
                bookmark_count=1,
                view_count=10,
                comment_count=0,
                width=10,
                height=10,
            )

    monkeypatch.setattr("app.api.admin.maintenance.engine_filter_count", _fake_engine_filter_count)
    monkeypatch.setattr("app.api.admin.maintenance.engine_pick", _fake_engine_pick)
    app.state.catalog_store = _FakeCatalog()

    with TestClient(app) as client:
        token = _login(client)
        resp = client.post(
            "/admin/api/maintenance/random-engine/compare-filters",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"filters": {"r18": 0}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["cardinality_match"] is False
        assert body["match"] is False
        assert body["python_filtered"] == 50
        assert body["engine_filtered"] == 40
        assert body["delta"] == 10


def test_compare_filters_engine_failure_502(tmp_path: Path, monkeypatch) -> None:
    app = _bootstrap_app(
        tmp_path,
        monkeypatch,
        name="compare_filters_502",
        engine_url="http://127.0.0.1:18091",
    )
    app.state.random_pick = _FakePick(count=1)

    async def _fake_engine_filter_count(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr("app.api.admin.maintenance.engine_filter_count", _fake_engine_filter_count)

    with TestClient(app) as client:
        token = _login(client)
        resp = client.post(
            "/admin/api/maintenance/random-engine/compare-filters",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={},
        )
        assert resp.status_code == 502
