from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_maintenance_openapi_documents_dual_run_and_cleanup() -> None:
    """Maintenance dual-run + cleanup ops must not rely on bare function-name summaries."""
    app = create_app()
    schema = app.openapi()
    paths = schema["paths"]

    engine_op = paths["/admin/api/maintenance/random-engine"]["get"]
    assert engine_op.get("summary") == "Random engine dual-run status"
    engine_desc = str(engine_op.get("description") or "")
    assert "circuit" in engine_desc.lower()
    assert "ready_for_traffic" in engine_desc

    snap_op = paths["/admin/api/maintenance/random-engine/snapshot"]["post"]
    assert snap_op.get("summary") == "Push random engine snapshot"
    assert "catalog" in str(snap_op.get("description") or "").lower()

    cleanup_op = paths["/admin/api/maintenance/request-logs/cleanup"]["post"]
    assert cleanup_op.get("summary") == "Cleanup request logs"
    assert "dry_run" in str(cleanup_op.get("description") or "")

    ports_op = paths["/admin/api/maintenance/modular-ports"]["get"]
    assert ports_op.get("summary") == "Modular ports status"
    ports_desc = str(ports_op.get("description") or "")
    for needle in ("catalog", "tags", "job_queue", "recent_dedup", "random_service", "random_pick"):
        assert needle in ports_desc


def test_admin_maintenance_openapi_documents_edge_and_rate_limit_readiness() -> None:
    """Phase-2/5 edge + RL ops surfaces must have explicit OpenAPI summaries (not Title-Case auto)."""
    app = create_app()
    schema = app.openapi()
    paths = schema["paths"]

    edge_op = paths["/admin/api/maintenance/image-edge"]["get"]
    assert edge_op.get("summary") == "Image Edge readiness status"
    edge_desc = str(edge_op.get("description") or "")
    assert "ready" in edge_desc.lower()
    assert "modules.image_edge" in edge_desc or "image_edge" in edge_desc

    cf_op = paths["/admin/api/maintenance/cf-api-proxy"]["get"]
    assert cf_op.get("summary") == "CF API proxy readiness status"
    cf_desc = str(cf_op.get("description") or "")
    assert "cf_api_proxy" in cf_desc or "Worker" in cf_desc

    r2_op = paths["/admin/api/maintenance/r2-prewarm"]["get"]
    assert r2_op.get("summary") == "R2 prewarm readiness status"
    r2_desc = str(r2_op.get("description") or "")
    assert "prewarm" in r2_desc.lower()
    assert "r2_prewarm" in r2_desc or "X-Prewarm-Secret" in r2_desc

    rl_op = paths["/admin/api/maintenance/api-key-rate-limit"]["get"]
    assert rl_op.get("summary") == "API key rate-limit readiness status"
    rl_desc = str(rl_op.get("description") or "")
    assert "memory" in rl_desc.lower()
    assert "redis" in rl_desc.lower() or "REDIS" in rl_desc

    cmp_op = paths["/admin/api/maintenance/random-engine/compare-filters"]["post"]
    assert cmp_op.get("summary") == "Compare random-engine filter cardinality (+ optional seeded pick probe)"
    cmp_desc = str(cmp_op.get("description") or "")
    assert "cardinality" in cmp_desc.lower() or "filtered" in cmp_desc.lower()


def test_admin_modular_ports_status_defaults(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_modular_ports.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.delenv("RECENT_DEDUP_BACKEND", raising=False)
    monkeypatch.delenv("JOB_QUEUE_BACKEND", raising=False)

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/modular-ports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["catalog"]["backend"] == "sqlite"
        assert body["tags"]["backend"] == "sqlite"
        assert body["job_queue"]["backend"] == "sqlite"
        assert body["job_queue"]["requested"] == "sqlite"
        assert body["job_queue"]["implemented"] is True
        assert "using_sqlite_fallback" not in body["job_queue"]
        assert body["recent_dedup"]["configured_backend"] == "memory"
        assert body["recent_dedup"]["active_backend"] == "memory"
        assert body["recent_dedup"]["redis_url_configured"] is False
        assert body["recent_dedup"]["using_memory_fallback"] is False
        assert body["random_service"]["backend"] == "default"
        assert body["random_pick"]["backend"] == "sqlite"


def test_admin_modular_ports_status_recent_redis_fallback(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_modular_ports_redis.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("RECENT_DEDUP_BACKEND", "redis")
    # Job queue redis/nats fail loud at settings load; keep implemented sqlite here.
    monkeypatch.setenv("JOB_QUEUE_BACKEND", "sqlite")

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/modular-ports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["recent_dedup"]["configured_backend"] == "redis"
        # No REDIS_URL → factory stays on memory (using_memory_fallback).
        assert body["recent_dedup"]["active_backend"] == "memory"
        assert body["recent_dedup"]["redis_url_configured"] is False
        assert body["recent_dedup"]["using_memory_fallback"] is True
        assert body["job_queue"]["backend"] == "sqlite"
        assert body["job_queue"]["requested"] == "sqlite"
        assert body["job_queue"]["implemented"] is True
        assert "using_sqlite_fallback" not in body["job_queue"]


def test_admin_modular_ports_status_recent_redis_active(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_modular_ports_redis_active.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")
    monkeypatch.setenv("RECENT_DEDUP_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    app = create_app()
    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.get(
            "/admin/api/maintenance/modular-ports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["recent_dedup"]["configured_backend"] == "redis"
        assert body["recent_dedup"]["active_backend"] == "redis"
        assert body["recent_dedup"]["redis_url_configured"] is True
        assert body["recent_dedup"]["using_memory_fallback"] is False
