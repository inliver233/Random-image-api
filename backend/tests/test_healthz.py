from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.public.healthz import router as healthz_router
from app.db.engine import create_engine
from app.db.models.base import Base
from app.core.time import iso_utc_ms


def test_healthz_ok_includes_request_id() -> None:
    app = FastAPI()
    app.state.engine = create_engine("sqlite+aiosqlite:///:memory:")
    app.include_router(healthz_router)

    client = TestClient(app)
    resp = client.get("/healthz")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["db_ok"] is True
    assert body["request_id"].startswith("req_")
    assert resp.headers["X-Request-Id"] == body["request_id"]
    modules = body.get("modules") or {}
    assert modules["image_edge"]["ready"] is False
    assert modules["image_edge"]["enabled_flag"] is False
    assert modules["cf_api_proxy"]["ready"] is False
    assert modules["cf_api_proxy"]["enabled_flag"] is False
    assert modules["cf_api_proxy"]["base_url_count"] == 0
    assert modules["cf_api_proxy"]["has_secret"] is False
    assert modules["random_engine"]["enabled"] is False
    assert modules["random_engine"]["url_configured"] is False
    assert modules["api_key_rate_limit"]["backend"] == "memory"
    assert modules["api_key_rate_limit"]["redis_url_configured"] is False
    assert modules["api_key_rate_limit"]["required"] is False
    assert modules["job_queue"]["backend"] == "sqlite"
    assert modules["job_queue"]["requested"] == "sqlite"
    assert modules["job_queue"]["implemented"] is True
    assert modules["job_queue"]["using_sqlite_fallback"] is False
    assert modules["catalog"]["backend"] == "sqlite"


def test_healthz_job_queue_reports_settings_fallback(tmp_path: Path, monkeypatch) -> None:
    """JOB_QUEUE_BACKEND via Settings: reserved nats → sqlite active + fallback honesty."""
    from app.main import create_app

    db_path = tmp_path / "healthz_job_queue.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("JOB_QUEUE_BACKEND", "nats")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        modules = resp.json().get("modules") or {}
        jq = modules.get("job_queue") or {}
        assert jq.get("backend") == "sqlite"
        assert jq.get("requested") == "nats"
        assert jq.get("implemented") is False
        assert jq.get("using_sqlite_fallback") is True


def test_healthz_uses_request_id_header_if_provided() -> None:
    app = FastAPI()
    app.state.engine = create_engine("sqlite+aiosqlite:///:memory:")
    app.include_router(healthz_router)

    client = TestClient(app)
    resp = client.get("/healthz", headers={"X-Request-Id": "req_test"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == "req_test"
    assert resp.headers["X-Request-Id"] == "req_test"


def test_healthz_reports_db_down_if_no_engine() -> None:
    app = FastAPI()
    app.include_router(healthz_router)

    client = TestClient(app)
    resp = client.get("/healthz", headers={"X-Request-Id": "req_test"})

    assert resp.status_code == 503
    body = resp.json()
    assert body["ok"] is False
    assert body["code"] == "INTERNAL_ERROR"
    assert body["request_id"] == "req_test"


def test_healthz_reports_worker_and_queue_status_when_available(tmp_path: Path) -> None:
    app = FastAPI()
    db_path = tmp_path / "healthz_deps.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    engine = create_engine(db_url)
    app.state.engine = engine
    app.include_router(healthz_router)

    async def _migrate_and_seed() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.exec_driver_sql(
                "INSERT INTO runtime_settings (key, value_json, description, updated_by) VALUES (?,?,?,?)",
                (
                    "worker.last_seen_at",
                    json.dumps({"at": iso_utc_ms(), "worker_id": "test", "pid": 1}),
                    "worker heartbeat",
                    "test",
                ),
            )
            await conn.exec_driver_sql(
                "INSERT INTO jobs (type, status, payload_json) VALUES ('import_urls','pending','{}')"
            )

    asyncio.run(_migrate_and_seed())

    client = TestClient(app)
    resp = client.get("/healthz")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["db_ok"] is True
    assert body["worker_ok"] is True
    assert body["queue_ok"] is True
    assert body["worker"]["last_seen_at"]
    assert body["queue"]["counts"]["pending"] == 1
    assert "modules" in body
    assert body["modules"]["image_edge"]["ready"] is False
    assert body["modules"]["random_service"]["backend"] == "default"
    assert body["modules"]["random_pick"]["backend"] == "sqlite"
    assert body["modules"]["catalog"]["backend"] == "sqlite"
