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


def test_admin_list_jobs_cursor_and_filters(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_list_jobs.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add_all(
                [
                    JobRow(
                        type="import_images",
                        status="pending",
                        priority=0,
                        run_after=None,
                        attempt=0,
                        max_attempts=3,
                        payload_json=json.dumps({"a": 1}, separators=(",", ":"), ensure_ascii=False),
                        last_error=None,
                        locked_by=None,
                        locked_at=None,
                        ref_type=None,
                        ref_id=None,
                    ),
                    JobRow(
                        type="proxy_probe",
                        status="failed",
                        priority=0,
                        run_after=None,
                        attempt=1,
                        max_attempts=3,
                        payload_json=json.dumps({"b": 2}, separators=(",", ":"), ensure_ascii=False),
                        last_error="err",
                        locked_by=None,
                        locked_at=None,
                        ref_type=None,
                        ref_id=None,
                    ),
                    JobRow(
                        type="import_images",
                        status="completed",
                        priority=0,
                        run_after=None,
                        attempt=1,
                        max_attempts=3,
                        payload_json=json.dumps({"c": 3}, separators=(",", ":"), ensure_ascii=False),
                        last_error=None,
                        locked_by=None,
                        locked_at=None,
                        ref_type=None,
                        ref_id=None,
                    ),
                ]
            )
            await session.commit()

    asyncio.run(_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp1 = client.get(
            "/admin/api/jobs",
            params={"limit": 2},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert body1["ok"] is True
        assert body1["request_id"] == "req_test"
        assert resp1.headers["X-Request-Id"] == "req_test"
        assert len(body1["items"]) == 2
        assert body1["next_cursor"].isdigit()

        first_id = int(body1["items"][0]["id"])
        second_id = int(body1["items"][1]["id"])
        assert first_id > second_id
        assert body1["next_cursor"] == str(second_id)

        resp2 = client.get(
            "/admin/api/jobs",
            params={"limit": 2, "cursor": body1["next_cursor"]},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["ok"] is True
        assert len(body2["items"]) == 1
        assert body2["next_cursor"] == ""

        resp3 = client.get(
            "/admin/api/jobs",
            params={"status": "failed"},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp3.status_code == 200
        body3 = resp3.json()
        assert body3["ok"] is True
        assert len(body3["items"]) == 1
        assert body3["items"][0]["status"] == "failed"

        resp4 = client.get(
            "/admin/api/jobs",
            params={"type": "import_images"},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp4.status_code == 200
        body4 = resp4.json()
        assert body4["ok"] is True
        assert len(body4["items"]) == 2
        assert {item["type"] for item in body4["items"]} == {"import_images"}

