from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_admin_api_keys_create_list_disable_and_reject_duplicate_name(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_api_keys.db"
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
            "/admin/api/api-keys",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"name": "key1", "api_key": "k_" + ("a" * 40), "description": "d1"},
        )
        assert create_resp.status_code == 200
        body = create_resp.json()
        assert body["ok"] is True
        assert body["api_key_id"].isdigit()
        assert body["hint"]
        assert "api_key" not in body

        list_resp = client.get(
            "/admin/api/api-keys",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert list_resp.status_code == 200
        list_body = list_resp.json()
        assert list_body["ok"] is True
        assert len(list_body["items"]) == 1
        item = list_body["items"][0]
        assert item["id"] == body["api_key_id"]
        assert item["name"] == "key1"
        assert item["enabled"] is True
        assert "key_hash" not in item

        disable_resp = client.put(
            f"/admin/api/api-keys/{body['api_key_id']}",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"enabled": False},
        )
        assert disable_resp.status_code == 200
        disable_body = disable_resp.json()
        assert disable_body["ok"] is True

        dup_resp = client.post(
            "/admin/api/api-keys",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"name": "key1", "api_key": "k_" + ("b" * 40)},
        )
        assert dup_resp.status_code == 400
        dup_body = dup_resp.json()
        assert dup_body["ok"] is False
        assert dup_body["code"] == "BAD_REQUEST"

