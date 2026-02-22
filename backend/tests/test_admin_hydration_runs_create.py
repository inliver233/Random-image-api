from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.hydration_runs import HydrationRun
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_create_hydration_run_creates_job(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_create_hydration_run.db"
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
            "/admin/api/hydration-runs",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"type": "backfill", "criteria": {"missing": ["tags"]}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["hydration_run_id"].isdigit()
        assert body["job_id"].isdigit()
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"

        run_id = int(body["hydration_run_id"])
        job_id = int(body["job_id"])

    async def _verify() -> None:
        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            run = await session.get(HydrationRun, run_id)
            assert run is not None
            assert run.status == "pending"
            assert run.type == "backfill"
            assert json.loads(run.criteria_json or "{}") == {"missing": ["tags"]}

            job = await session.get(JobRow, job_id)
            assert job is not None
            assert job.status == "pending"
            assert job.type == "hydrate_metadata"
            assert job.ref_type == "hydration_run"
            assert job.ref_id == str(run_id)

    asyncio.run(_verify())

