from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.core.random_engine_pick import classify_engine_pick_response, try_pick_via_engine
from app.main import create_app


def test_classify_engine_pick_response_empty_index() -> None:
    assert classify_engine_pick_response(None) == "unavailable"
    assert classify_engine_pick_response({"ok": False, "code": "ERR"}) == "not_ok"
    assert classify_engine_pick_response({"ok": True, "code": "INDEX_NOT_READY", "items": []}) == "empty_index"
    assert (
        classify_engine_pick_response(
            {"ok": True, "code": "NO_MATCH", "items": [], "debug": {"reason": "empty_index"}}
        )
        == "empty_index"
    )
    assert classify_engine_pick_response({"ok": True, "code": "NO_MATCH", "items": []}) == "no_match"
    assert classify_engine_pick_response({"ok": True, "code": "OK", "items": [{"id": 1}]}) == "ok"


def test_try_pick_via_engine_maps_index_not_ready(monkeypatch) -> None:
    async def _fake_pick(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": True, "code": "INDEX_NOT_READY", "items": []}

    monkeypatch.setattr("app.core.random_engine_pick.engine_pick", _fake_pick)

    async def _run() -> None:
        image, meta = await try_pick_via_engine(
            client=object(),
            base_url="http://engine.test",
            session=object(),  # type: ignore[arg-type]
            payload={"filters": {}, "strategy": "random", "limit": 1},
        )
        assert image is None
        assert meta["engine_status"] == "empty_index"
        assert meta["engine_code"] == "INDEX_NOT_READY"

    asyncio.run(_run())


def test_admin_random_engine_status_empty_index_warning(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_engine_empty.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("RANDOM_ENGINE_ENABLED", "1")
    monkeypatch.setenv("RANDOM_ENGINE_URL", "http://127.0.0.1:18091")
    monkeypatch.setenv("RANDOM_ENGINE_TRAFFIC_PERCENT", "100")

    async def _fake_health(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": True, "service": "random-engine", "index_size": 0, "snapshot_revision": "none"}

    monkeypatch.setattr("app.api.admin.maintenance.engine_health", _fake_health)

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]
        resp = client.get(
            "/admin/api/maintenance/random-engine",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["healthy"] is True
        assert body["index_size"] == 0
        assert body["index_empty"] is True
        assert body["ready_for_traffic"] is False
        assert "empty" in str(body.get("cutover_warning") or "").lower()
        # Dual-run circuit snapshot is always present for ops honesty.
        assert isinstance(body.get("circuit"), dict)
        assert body["circuit"].get("state") in {"closed", "open", "half_open"}
        assert "consecutive_failures" in body["circuit"]


def test_admin_random_engine_status_circuit_open_warning(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_engine_circuit.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("RANDOM_ENGINE_ENABLED", "1")
    monkeypatch.setenv("RANDOM_ENGINE_URL", "http://127.0.0.1:18091")
    monkeypatch.setenv("RANDOM_ENGINE_TRAFFIC_PERCENT", "100")

    async def _fake_health(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "service": "random-engine",
            "ready": True,
            "index_size": 12,
            "snapshot_revision": "rev1",
        }

    def _fake_circuit() -> dict[str, Any]:
        # Isolate from process-global circuit mutations during app lifespan.
        return {
            "state": "open",
            "consecutive_failures": 5,
            "open_remaining_s": 18.0,
            "failure_threshold": 5,
            "open_s": 30,
        }

    monkeypatch.setattr("app.api.admin.maintenance.engine_health", _fake_health)
    monkeypatch.setattr("app.api.admin.maintenance.engine_circuit_snapshot", _fake_circuit)

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]
        resp = client.get(
            "/admin/api/maintenance/random-engine",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["healthy"] is True
        assert body["index_empty"] is False
        assert body["ready_for_traffic"] is True
        assert body["circuit"]["state"] == "open"
        assert body["circuit"]["consecutive_failures"] == 5
        assert float(body["circuit"]["open_remaining_s"]) == 18.0
        warn = str(body.get("cutover_warning") or "")
        assert "circuit open" in warn.lower()
        assert "fail-open" in warn.lower()


def test_admin_random_engine_status_nonempty_legacy_health_is_not_ready(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "admin_engine_legacy_health.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("RANDOM_ENGINE_ENABLED", "1")
    monkeypatch.setenv("RANDOM_ENGINE_URL", "http://127.0.0.1:18091")
    monkeypatch.setenv("RANDOM_ENGINE_TRAFFIC_PERCENT", "100")

    async def _fake_health(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": True, "service": "random-engine", "index_size": 12, "snapshot_revision": "legacy"}

    monkeypatch.setattr("app.api.admin.maintenance.engine_health", _fake_health)
    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]
        resp = client.get(
            "/admin/api/maintenance/random-engine",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["index_size"] == 12
        assert body["engine_ready"] is False
        assert body["ready_for_traffic"] is False
        assert "complete verified snapshot" in str(body["cutover_warning"]).lower()
