from __future__ import annotations

import asyncio
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.db.models.request_logs import RequestLog
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_request_logs_cleanup_dry_run_and_delete(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_request_logs_cleanup.db"
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
                    RequestLog(
                        created_at="2000-01-01T00:00:00.000Z",
                        request_id="req_old1",
                        method="GET",
                        route="/healthz",
                        status=200,
                        duration_ms=1,
                        ip="127.0.0.1",
                        user_agent="pytest",
                        sample_rate=1.0,
                    ),
                    RequestLog(
                        created_at="2000-01-02T00:00:00.000Z",
                        request_id="req_old2",
                        method="GET",
                        route="/healthz",
                        status=200,
                        duration_ms=1,
                        ip="127.0.0.1",
                        user_agent="pytest",
                        sample_rate=1.0,
                    ),
                    RequestLog(
                        created_at="2000-01-03T00:00:00.000Z",
                        request_id="req_old3",
                        method="GET",
                        route="/healthz",
                        status=200,
                        duration_ms=1,
                        ip="127.0.0.1",
                        user_agent="pytest",
                        sample_rate=1.0,
                    ),
                    RequestLog(
                        created_at="2099-01-01T00:00:00.000Z",
                        request_id="req_new",
                        method="GET",
                        route="/healthz",
                        status=200,
                        duration_ms=1,
                        ip="127.0.0.1",
                        user_agent="pytest",
                        sample_rate=1.0,
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
            "/admin/api/maintenance/request-logs/cleanup",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"keep_days": 365, "max_delete_rows": 2, "chunk_size": 1, "dry_run": True},
        )
        assert preview.status_code == 200
        body_preview = preview.json()
        assert body_preview["ok"] is True
        assert body_preview["dry_run"] is True
        assert body_preview["would_delete"] == 2
        assert body_preview["has_more"] is True

        cleanup1 = client.post(
            "/admin/api/maintenance/request-logs/cleanup",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"keep_days": 365, "max_delete_rows": 2, "chunk_size": 1},
        )
        assert cleanup1.status_code == 200
        body1 = cleanup1.json()
        assert body1["ok"] is True
        assert body1["dry_run"] is False
        assert body1["deleted"] == 2
        assert body1["has_more"] is True

        cleanup2 = client.post(
            "/admin/api/maintenance/request-logs/cleanup",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"keep_days": 365, "max_delete_rows": 10, "chunk_size": 10},
        )
        assert cleanup2.status_code == 200
        body2 = cleanup2.json()
        assert body2["deleted"] == 1
        assert body2["has_more"] is False

    async def _assert_remaining() -> None:
        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            rows = (await session.execute(sa.select(RequestLog).order_by(RequestLog.id.asc()))).scalars().all()
            assert len(rows) == 1
            assert rows[0].request_id == "req_new"

        await app.state.engine.dispose()

    asyncio.run(_assert_remaining())

