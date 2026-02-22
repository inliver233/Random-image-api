from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_admin_proxies_probe_enqueues_job(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_proxies_probe.db"
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

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/proxies/probe",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["job_id"].isdigit()
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"

        job_id = int(body["job_id"])

        async def _fetch_job() -> tuple[str, str]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT type, status FROM jobs WHERE id = ?",
                    (job_id,),
                )
                row = result.fetchone()
                assert row is not None
                return (str(row[0]), str(row[1]))

        job_type, status = asyncio.run(_fetch_job())
        assert job_type == "proxy_probe"
        assert status == "pending"

