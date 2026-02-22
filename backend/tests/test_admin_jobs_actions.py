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


def test_admin_job_retry_cancel_move_to_dlq(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_job_actions.db"
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
            j1 = JobRow(
                type="import_images",
                status="dlq",
                priority=0,
                run_after=None,
                attempt=3,
                max_attempts=3,
                payload_json=json.dumps({"a": 1}, separators=(",", ":"), ensure_ascii=False),
                last_error="err",
                locked_by=None,
                locked_at=None,
                ref_type=None,
                ref_id=None,
            )
            j2 = JobRow(
                type="proxy_probe",
                status="pending",
                priority=0,
                run_after=None,
                attempt=0,
                max_attempts=3,
                payload_json=json.dumps({"b": 2}, separators=(",", ":"), ensure_ascii=False),
                last_error=None,
                locked_by=None,
                locked_at=None,
                ref_type=None,
                ref_id=None,
            )
            j3 = JobRow(
                type="hydrate_metadata",
                status="failed",
                priority=0,
                run_after=None,
                attempt=1,
                max_attempts=3,
                payload_json=json.dumps({"c": 3}, separators=(",", ":"), ensure_ascii=False),
                last_error="boom",
                locked_by=None,
                locked_at=None,
                ref_type=None,
                ref_id=None,
            )
            session.add_all([j1, j2, j3])
            await session.commit()
            await session.refresh(j1)
            await session.refresh(j2)
            await session.refresh(j3)

            nonlocal ids
            ids = {"retry": int(j1.id), "cancel": int(j2.id), "dlq": int(j3.id)}

    asyncio.run(_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        r1 = client.post(
            f"/admin/api/jobs/{ids['retry']}/retry",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert r1.status_code == 200
        b1 = r1.json()
        assert b1["ok"] is True
        assert b1["status"] == "pending"
        assert b1["job_id"] == str(ids["retry"])

        r2 = client.post(
            f"/admin/api/jobs/{ids['cancel']}/cancel",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["ok"] is True
        assert b2["status"] == "canceled"
        assert b2["job_id"] == str(ids["cancel"])

        r3 = client.post(
            f"/admin/api/jobs/{ids['dlq']}/move-to-dlq",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert r3.status_code == 200
        b3 = r3.json()
        assert b3["ok"] is True
        assert b3["status"] == "dlq"
        assert b3["job_id"] == str(ids["dlq"])

        l = client.get(
            "/admin/api/jobs",
            params={"limit": 10},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert l.status_code == 200
        items = {int(it["id"]): it for it in l.json()["items"]}
        assert items[ids["retry"]]["status"] == "pending"
        assert items[ids["cancel"]]["status"] == "canceled"
        assert items[ids["dlq"]]["status"] == "dlq"

