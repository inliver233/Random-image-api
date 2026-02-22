from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_admin_update_proxy_pool(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_update_proxy_pool.db"
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
            json={"name": "pool1"},
        )
        pool_id = int(create_resp.json()["pool_id"])

        update_resp = client.put(
            f"/admin/api/proxy-pools/{pool_id}",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"name": "pool2", "enabled": False},
        )
        assert update_resp.status_code == 200
        body = update_resp.json()
        assert body["ok"] is True
        assert body["pool_id"] == str(pool_id)
        assert body["request_id"] == "req_test"

        list_resp = client.get(
            "/admin/api/proxy-pools",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        items = list_resp.json()["items"]
        assert items[0]["name"] == "pool2"
        assert items[0]["enabled"] is False


def test_admin_update_proxy_pool_not_found_returns_404(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_update_proxy_pool_not_found.db"
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
        resp = client.put(
            "/admin/api/proxy-pools/999",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"name": "poolx"},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "NOT_FOUND"
        assert body["request_id"] == "req_test"

