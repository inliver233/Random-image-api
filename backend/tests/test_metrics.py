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
        assert "new_pixiv_image_delivery_total" in text
        assert "new_pixiv_upstream_stream_errors_total" in text
        assert "new_pixiv_jobs_claim_total" in text
        assert "new_pixiv_jobs_failed_total" in text
        assert "new_pixiv_token_refresh_fail_total" in text
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
