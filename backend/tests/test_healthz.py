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
