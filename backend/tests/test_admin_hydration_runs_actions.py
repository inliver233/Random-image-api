from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.hydration_runs import HydrationRun
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_hydration_run_pause_resume_cancel(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_hydration_run_actions.db"
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
        created = client.post(
            "/admin/api/hydration-runs",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"type": "backfill", "criteria": {}},
        )
        assert created.status_code == 200
        body0 = created.json()
        run_id = int(body0["hydration_run_id"])
        job_id = int(body0["job_id"])

        paused = client.post(
            f"/admin/api/hydration-runs/{run_id}/pause",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"

        resumed = client.post(
            f"/admin/api/hydration-runs/{run_id}/resume",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "pending"

        canceled = client.post(
            f"/admin/api/hydration-runs/{run_id}/cancel",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert canceled.status_code == 200
        assert canceled.json()["status"] == "canceled"

    async def _verify() -> None:
        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            run = await session.get(HydrationRun, run_id)
            assert run is not None
            assert run.status == "canceled"

            job = await session.get(JobRow, job_id)
            assert job is not None
            assert job.status == "canceled"

    asyncio.run(_verify())

