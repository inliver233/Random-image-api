from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_jobs_cleanup_dry_run_and_delete(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_jobs_cleanup.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add_all(
                [
                    JobRow(
                        created_at="2000-01-01T00:00:00.000Z",
                        updated_at="2000-01-01T00:00:00.000Z",
                        type="hydrate_metadata",
                        status="completed",
                        payload_json="{}",
                    ),
                    JobRow(
                        created_at="2000-01-02T00:00:00.000Z",
                        updated_at="2000-01-02T00:00:00.000Z",
                        type="hydrate_metadata",
                        status="failed",
                        payload_json="{}",
                    ),
                    JobRow(
                        created_at="2000-01-03T00:00:00.000Z",
                        updated_at="2000-01-03T00:00:00.000Z",
                        type="import_images",
                        status="pending",
                        payload_json="{}",
                    ),
                    JobRow(
                        created_at="2099-01-01T00:00:00.000Z",
                        updated_at="2099-01-01T00:00:00.000Z",
                        type="hydrate_metadata",
                        status="completed",
                        payload_json="{}",
                    ),
                ]
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        preview = client.post(
            "/admin/api/maintenance/jobs/cleanup",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"keep_days": 365, "max_delete_rows": 10, "chunk_size": 1, "dry_run": True},
        )
        assert preview.status_code == 200
        body_preview = preview.json()
        assert body_preview["ok"] is True
        assert body_preview["dry_run"] is True
        assert body_preview["would_delete"] == 2
        assert "completed" in body_preview["terminal_statuses"]

        deleted = client.post(
            "/admin/api/maintenance/jobs/cleanup",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"keep_days": 365, "max_delete_rows": 10, "chunk_size": 1, "dry_run": False},
        )
        assert deleted.status_code == 200
        body = deleted.json()
        assert body["ok"] is True
        assert body["dry_run"] is False
        assert body["deleted"] == 2
        assert body["has_more"] is False
