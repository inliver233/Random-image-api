from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_job_detail_returns_payload(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_job_detail.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    ids: dict[str, int] = {}

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            job = JobRow(
                type="proxy_probe",
                status="failed",
                priority=0,
                run_after=None,
                attempt=1,
                max_attempts=3,
                payload_json=json.dumps({"a": 1}, separators=(",", ":"), ensure_ascii=False),
                last_error="boom",
                locked_by=None,
                locked_at=None,
                ref_type="test",
                ref_id="r1",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            ids["job"] = int(job.id)

    asyncio.run(_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.get(
            f"/admin/api/jobs/{ids['job']}",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"
        assert body["item"]["id"] == str(ids["job"])
        assert body["item"]["status"] == "failed"
        assert body["item"]["payload"] == {"a": 1}
        assert body["item"]["payload_json"] == '{"a":1}'

        missing = client.get(
            "/admin/api/jobs/999999",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert missing.status_code == 404
        assert missing.json()["ok"] is False

