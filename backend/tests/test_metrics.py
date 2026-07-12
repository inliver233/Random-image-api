from __future__ import annotations

import asyncio
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_metrics_requires_admin_auth(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "metrics_auth.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    with TestClient(app) as client:
        resp = client.get("/metrics", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 401
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "UNAUTHORIZED"
        assert body["request_id"] == "req_test"


def test_metrics_exposes_random_jobs_and_proxy_metrics(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "metrics_basic.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _migrate_and_seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.exec_driver_sql(
                "INSERT INTO jobs (type, status, payload_json) VALUES ('import_urls','pending','{}')"
            )
            await conn.exec_driver_sql(
                "INSERT INTO proxy_endpoints (scheme, host, port) VALUES ('http','example.com',8080)"
            )

    asyncio.run(_migrate_and_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

        text = resp.text
        assert "new_pixiv_random_requests_total" in text
        assert "new_pixiv_random_no_match_total" in text
        assert "new_pixiv_random_opportunistic_hydrate_enqueued_total" in text
        assert "new_pixiv_random_engine_pick_total" in text
        assert "new_pixiv_random_engine_pick_latency_seconds" in text
        assert 'status="empty_index"' in text or 'status="empty_index"' in text.replace("'", '"')
        # Dual-run honesty labels must be pre-registered (zero series) for scrape dashboards.
        for skip_status in ("skipped_traffic", "skipped_sticky", "skipped_circuit"):
            assert f'status="{skip_status}"' in text
        # Process circuit gauges (local snapshot on scrape).
        assert "new_pixiv_random_engine_circuit_state" in text
        assert 'state="closed"' in text
        assert "new_pixiv_random_engine_circuit_open_remaining_seconds" in text
        assert "new_pixiv_random_engine_circuit_consecutive_failures" in text
        # Modular readiness gauges (local config snapshot; pre-registered zero series).
        assert "new_pixiv_module_readiness" in text
        assert "new_pixiv_module_base_url_count" in text
        for module in (
            "image_edge",
            "cf_api_proxy",
            "r2_prewarm",
            "api_key_rate_limit",
            "job_queue",
            "recent_dedup",
        ):
            assert f'module="{module}"' in text
        # Defaults: flags off; job_queue implemented (sqlite).
        # prometheus_client sorts label names alphabetically (flag before module).
        assert re.search(
            r'new_pixiv_module_readiness\{flag="enabled",module="image_edge"\}\s+0(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_readiness\{flag="implemented",module="job_queue"\}\s+1(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_base_url_count\{module="image_edge"\}\s+0(\.0+)?\b',
            text,
        )
        assert "new_pixiv_image_delivery_total" in text
        assert "new_pixiv_upstream_stream_errors_total" in text
        assert "new_pixiv_jobs_claim_total" in text
        assert "new_pixiv_jobs_failed_total" in text
        assert "new_pixiv_token_refresh_fail_total" in text
        assert "new_pixiv_pixiv_api_egress_total" in text
        assert 'via="cf"' in text
        assert 'via="residential"' in text
        assert "new_pixiv_r2_prewarm_total" in text
        assert 'result="skipped_disabled"' in text or 'result="ok"' in text
        assert "new_pixiv_jobs_status_count" in text
        assert "new_pixiv_proxy_endpoints_state_count" in text
        assert "new_pixiv_proxy_probe_latency_ms" in text

        assert re.search(r'new_pixiv_jobs_status_count\{status=\"pending\"\}\s+1(\.0+)?\b', text)
        assert re.search(r'new_pixiv_proxy_endpoints_state_count\{state=\"enabled\"\}\s+1(\.0+)?\b', text)


def test_metrics_exposes_open_dual_run_circuit(tmp_path: Path, monkeypatch) -> None:
    """Open process circuit is visible on /metrics gauges (local snapshot)."""
    from app.core.random_engine_client import engine_circuit_record, reset_engine_circuit_for_tests

    db_path = tmp_path / "metrics_circuit.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    reset_engine_circuit_for_tests()
    for _ in range(5):
        engine_circuit_record("unavailable")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    try:
        with TestClient(app) as client:
            resp = client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            text = resp.text
            assert re.search(
                r'new_pixiv_random_engine_circuit_state\{state=\"open\"\}\s+1(\.0+)?\b',
                text,
            )
            assert re.search(
                r'new_pixiv_random_engine_circuit_open_remaining_seconds\s+[1-9]',
                text,
            )
    finally:
        reset_engine_circuit_for_tests()


def test_metrics_exposes_modular_readiness_when_edge_ready(tmp_path: Path, monkeypatch) -> None:
    """Image edge + r2 prewarm readiness flags flip on when config is complete."""
    db_path = tmp_path / "metrics_modular.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("IMAGE_EDGE_ENABLED", "true")
    monkeypatch.setenv("IMAGE_EDGE_BASE_URLS", "https://img-a.example,https://img-b.example")
    monkeypatch.setenv("IMAGE_EDGE_SECRET", "edge-secret-test")
    monkeypatch.setenv("R2_PREWARM_ENABLED", "true")
    monkeypatch.setenv("R2_PREWARM_URL", "https://prewarm.example/hook")
    monkeypatch.setenv("R2_PREWARM_SECRET", "prewarm-secret")
    monkeypatch.setenv("PUBLIC_API_KEY_REQUIRED", "true")
    monkeypatch.setenv("PUBLIC_API_KEY_RATE_LIMIT_BACKEND", "redis")
    # No REDIS_URL → memory fallback honesty.

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        text = resp.text
        # prometheus_client sorts label names alphabetically (flag before module).
        assert re.search(
            r'new_pixiv_module_readiness\{flag="enabled",module="image_edge"\}\s+1(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_readiness\{flag="ready",module="image_edge"\}\s+1(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_base_url_count\{module="image_edge"\}\s+2(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_readiness\{flag="ready",module="r2_prewarm"\}\s+1(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_readiness\{flag="secret_configured",module="r2_prewarm"\}\s+1(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_readiness\{flag="required",module="api_key_rate_limit"\}\s+1(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_readiness\{flag="using_memory_fallback",module="api_key_rate_limit"\}\s+1(\.0+)?\b',
            text,
        )


def test_metrics_base_url_count_raw_when_edge_not_ready(tmp_path: Path, monkeypatch) -> None:
    """Configured bases still count on scrape when secret missing (not ready)."""
    db_path = tmp_path / "metrics_edge_bases.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("IMAGE_EDGE_ENABLED", "true")
    monkeypatch.setenv("IMAGE_EDGE_BASE_URLS", "https://img-a.example,https://img-b.example")
    monkeypatch.delenv("IMAGE_EDGE_SECRET", raising=False)
    monkeypatch.setenv("R2_PREWARM_ENABLED", "true")
    monkeypatch.setenv("R2_PREWARM_URL", "https://prewarm.example/hook")
    monkeypatch.delenv("R2_PREWARM_SECRET", raising=False)
    monkeypatch.delenv("PREWARM_SECRET", raising=False)

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.get("/metrics", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        text = resp.text
        assert re.search(
            r'new_pixiv_module_readiness\{flag="enabled",module="image_edge"\}\s+1(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_readiness\{flag="ready",module="image_edge"\}\s+0(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_base_url_count\{module="image_edge"\}\s+2(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_readiness\{flag="url_configured",module="r2_prewarm"\}\s+1(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_readiness\{flag="secret_configured",module="r2_prewarm"\}\s+0(\.0+)?\b',
            text,
        )
        assert re.search(
            r'new_pixiv_module_readiness\{flag="ready",module="r2_prewarm"\}\s+0(\.0+)?\b',
            text,
        )


def test_metrics_openapi_documents_modular_scrape() -> None:
    """Admin /metrics must document dual-run circuit + modular readiness scrape behavior."""
    app = create_app()
    schema = app.openapi()
    op = schema["paths"]["/metrics"]["get"]
    assert op.get("summary") == "Prometheus metrics scrape"
    desc = str(op.get("description") or "")
    assert "new_pixiv_module_readiness" in desc
    assert "circuit" in desc.lower()
    assert "/healthz" in desc or "healthz" in desc
    assert "dialect-aware" in desc.lower() or "named binds" in desc.lower()
    assert "new_pixiv_pixiv_api_egress_total" in desc
    assert "new_pixiv_r2_prewarm_total" in desc
    assert "local_i_redirect" in desc
