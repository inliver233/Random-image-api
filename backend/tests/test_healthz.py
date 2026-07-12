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
    assert modules["r2_prewarm"]["enabled_flag"] is False
    assert modules["r2_prewarm"]["ready"] is False
    assert modules["r2_prewarm"]["url_configured"] is False
    assert modules["r2_prewarm"]["secret_configured"] is False
    assert modules["random_engine"]["enabled"] is False
    assert modules["random_engine"]["url_configured"] is False
    circuit = modules["random_engine"].get("circuit") or {}
    assert isinstance(circuit, dict)
    assert circuit.get("state") in {"closed", "open", "half_open"}
    assert "consecutive_failures" in circuit
    assert modules["random_service"]["backend"] == "default"
    assert modules["random_pick"]["backend"] == "sqlite"
    assert modules["api_key_rate_limit"]["backend"] == "memory"
    assert modules["api_key_rate_limit"]["requested"] == "memory"
    assert modules["api_key_rate_limit"]["redis_url_configured"] is False
    assert modules["api_key_rate_limit"]["required"] is False
    assert modules["api_key_rate_limit"]["using_memory_fallback"] is False
    assert modules["job_queue"]["backend"] == "sqlite"
    assert modules["job_queue"]["requested"] == "sqlite"
    assert modules["job_queue"]["implemented"] is True
    assert "using_sqlite_fallback" not in modules["job_queue"]
    assert modules["catalog"]["backend"] == "sqlite"
    assert modules["tags"]["backend"] == "sqlite"
    assert modules["recent_dedup"]["backend"] == "memory"
    assert modules["recent_dedup"]["requested"] == "memory"
    assert modules["recent_dedup"]["using_memory_fallback"] is False


def test_healthz_job_queue_rejects_reserved_backend(tmp_path: Path, monkeypatch) -> None:
    """JOB_QUEUE_BACKEND=nats/redis must fail at settings/boot (no silent sqlite fallback)."""
    from app.main import create_app

    db_path = tmp_path / "healthz_job_queue.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("JOB_QUEUE_BACKEND", "nats")

    try:
        create_app()
        raise AssertionError("expected ValueError for reserved JOB_QUEUE_BACKEND")
    except ValueError as exc:
        msg = str(exc).lower()
        assert "not implemented" in msg or "reserved" in msg


def test_healthz_job_queue_memory_alias_label(tmp_path: Path, monkeypatch) -> None:
    """JOB_QUEUE_BACKEND=memory → active backend label memory (implemented, no fallback)."""
    from app.main import create_app

    db_path = tmp_path / "healthz_job_queue_memory.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("JOB_QUEUE_BACKEND", "memory")

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
        assert jq.get("backend") == "memory"
        assert jq.get("requested") == "memory"
        assert jq.get("implemented") is True
        assert "using_sqlite_fallback" not in jq


def test_healthz_recent_dedup_reports_settings_fallback(tmp_path: Path, monkeypatch) -> None:
    """RECENT_DEDUP_BACKEND=redis without REDIS_URL → memory active + fallback honesty."""
    from app.main import create_app

    db_path = tmp_path / "healthz_recent_dedup.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("RECENT_DEDUP_BACKEND", "redis")
    # No REDIS_URL → factory falls back to memory.

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        modules = resp.json().get("modules") or {}
        rd = modules.get("recent_dedup") or {}
        assert rd.get("backend") == "memory"
        assert rd.get("requested") == "redis"
        assert rd.get("using_memory_fallback") is True


def test_healthz_random_engine_circuit_snapshot(monkeypatch) -> None:
    """/healthz modules.random_engine.circuit is process-local (no outbound probe)."""
    from app.core.random_engine_client import reset_engine_circuit_for_tests

    reset_engine_circuit_for_tests()

    def _fake_circuit() -> dict:
        return {
            "state": "open",
            "consecutive_failures": 5,
            "open_remaining_s": 9.5,
            "failure_threshold": 5,
            "open_s": 30,
        }

    monkeypatch.setattr("app.api.public.healthz.engine_circuit_snapshot", _fake_circuit)

    app = FastAPI()
    app.state.engine = create_engine("sqlite+aiosqlite:///:memory:")
    app.include_router(healthz_router)

    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    circuit = (resp.json().get("modules") or {}).get("random_engine", {}).get("circuit") or {}
    assert circuit.get("state") == "open"
    assert circuit.get("consecutive_failures") == 5
    assert float(circuit.get("open_remaining_s") or 0) == 9.5
    reset_engine_circuit_for_tests()


def test_healthz_api_key_rate_limit_reports_settings_fallback(tmp_path: Path, monkeypatch) -> None:
    """PUBLIC_API_KEY_RATE_LIMIT_BACKEND=redis without REDIS_URL → memory active + fallback honesty."""
    from app.main import create_app

    db_path = tmp_path / "healthz_api_key_rl.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("PUBLIC_API_KEY_RATE_LIMIT_BACKEND", "redis")
    # No REDIS_URL → factory falls back to memory.

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        modules = resp.json().get("modules") or {}
        rl = modules.get("api_key_rate_limit") or {}
        assert rl.get("backend") == "memory"
        assert rl.get("requested") == "redis"
        assert rl.get("using_memory_fallback") is True
        assert rl.get("redis_url_configured") is False


def test_healthz_r2_prewarm_ready_requires_secret(tmp_path: Path, monkeypatch) -> None:
    """enabled+url without secret → not ready (parity with admin r2-prewarm)."""
    from app.main import create_app

    db_path = tmp_path / "healthz_r2_no_secret.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("R2_PREWARM_ENABLED", "1")
    monkeypatch.setenv("R2_PREWARM_URL", "https://prewarm.example.com/hook")
    monkeypatch.delenv("R2_PREWARM_SECRET", raising=False)
    monkeypatch.delenv("IMAGE_EDGE_SECRET", raising=False)
    monkeypatch.delenv("PREWARM_SECRET", raising=False)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        r2 = (resp.json().get("modules") or {}).get("r2_prewarm") or {}
        assert r2.get("enabled_flag") is True
        assert r2.get("url_configured") is True
        assert r2.get("secret_configured") is False
        assert r2.get("ready") is False


def test_healthz_r2_prewarm_ready_with_secret(tmp_path: Path, monkeypatch) -> None:
    """enabled+url+secret → ready; never leak secret material."""
    from app.main import create_app

    db_path = tmp_path / "healthz_r2_ready.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("R2_PREWARM_ENABLED", "1")
    monkeypatch.setenv("R2_PREWARM_URL", "https://prewarm.example.com/hook?token=super-secret")
    monkeypatch.setenv("IMAGE_EDGE_SECRET", "edge-secret-for-prewarm")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_seed())

    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 200
        body = resp.json()
        r2 = (body.get("modules") or {}).get("r2_prewarm") or {}
        assert r2.get("enabled_flag") is True
        assert r2.get("url_configured") is True
        assert r2.get("secret_configured") is True
        assert r2.get("ready") is True
        assert "super-secret" not in str(body)
        assert "edge-secret-for-prewarm" not in str(body)


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
