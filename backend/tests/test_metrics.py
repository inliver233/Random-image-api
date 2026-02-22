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
        assert "new_pixiv_upstream_stream_errors_total" in text
        assert "new_pixiv_jobs_claim_total" in text
        assert "new_pixiv_jobs_failed_total" in text
        assert "new_pixiv_token_refresh_fail_total" in text
        assert "new_pixiv_jobs_status_count" in text
        assert "new_pixiv_proxy_endpoints_state_count" in text
        assert "new_pixiv_proxy_probe_latency_ms" in text

        assert re.search(r'new_pixiv_jobs_status_count\{status=\"pending\"\}\s+1(\.0+)?\b', text)
        assert re.search(r'new_pixiv_proxy_endpoints_state_count\{state=\"enabled\"\}\s+1(\.0+)?\b', text)
