from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.admin_audit import AdminAudit
from app.db.models.base import Base
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_audit_list_cursor_and_parses_detail_json(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_audit_list.db"
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
                    AdminAudit(
                        actor="admin",
                        action="POST",
                        resource="/admin/api/proxy-pools",
                        record_id=None,
                        request_id="req_1",
                        ip="1.1.1.1",
                        user_agent="ua",
                        detail_json=json.dumps({"a": 1}, ensure_ascii=False, separators=(",", ":")),
                    ),
                    AdminAudit(
                        actor="admin",
                        action="PUT",
                        resource="/admin/api/proxies/endpoints/1",
                        record_id="1",
                        request_id="req_2",
                        ip=None,
                        user_agent=None,
                        detail_json=None,
                    ),
                    AdminAudit(
                        actor=None,
                        action="POST",
                        resource="/admin/api/login",
                        record_id=None,
                        request_id="req_3",
                        ip=None,
                        user_agent=None,
                        detail_json="{not_json",
                    ),
                ]
            )
            await session.commit()

    asyncio.run(_seed())

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp1 = client.get(
            "/admin/api/audit",
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

        assert body1["items"][0]["detail_json"] == {"raw": "{not_json"}
        assert body1["items"][1]["detail_json"] is None

        resp2 = client.get(
            "/admin/api/audit",
            params={"limit": 5, "cursor": body1["next_cursor"]},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["ok"] is True
        assert len(body2["items"]) == 1
        assert body2["next_cursor"] == ""
        assert body2["items"][0]["detail_json"] == {"a": 1}


def test_admin_audit_middleware_records_write_ops(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_audit_middleware.db"
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
            "/admin/api/proxy-pools",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"name": "pool1", "description": None, "enabled": True},
        )
        assert resp.status_code == 200

        async def _fetch_audit() -> tuple[int, str, str, str]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT id, action, resource, request_id FROM admin_audit ORDER BY id ASC",
                )
                rows = result.fetchall()
                assert len(rows) == 1
                return (int(rows[0][0]), str(rows[0][1]), str(rows[0][2]), str(rows[0][3]))

        audit_id, action, resource, request_id = asyncio.run(_fetch_audit())
        assert audit_id > 0
        assert action == "POST"
        assert resource == "/admin/api/proxy-pools"
        assert request_id == "req_test"
