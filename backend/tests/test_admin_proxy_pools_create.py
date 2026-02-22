from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_admin_create_proxy_pool_and_reject_duplicate_name(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_create_proxy_pool.db"
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
        create_resp = client.post(
            "/admin/api/proxy-pools",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"name": "pool1", "description": "d1", "enabled": True},
        )
        assert create_resp.status_code == 200
        body1 = create_resp.json()
        assert body1["ok"] is True
        assert body1["pool_id"].isdigit()
        assert body1["request_id"] == "req_test"
        assert create_resp.headers["X-Request-Id"] == "req_test"

        dup_resp = client.post(
            "/admin/api/proxy-pools",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"name": "pool1"},
        )
        assert dup_resp.status_code == 400
        body2 = dup_resp.json()
        assert body2["ok"] is False
        assert body2["code"] == "BAD_REQUEST"
        assert body2["request_id"] == "req_test"

