from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_admin_get_import_returns_item_and_job(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_get_import.db"
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
        text = "\n".join(
            [
                "https://i.pximg.net/img-original/img/2023/01/01/00/00/00/12345678_p0.jpg",
                "https://www.pixiv.net/artworks/12345678",
            ]
        )

        create_resp = client.post(
            "/admin/api/imports",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"text": text, "dry_run": False, "hydrate_on_import": False, "source": "manual"},
        )
        assert create_resp.status_code == 200
        import_id = int(create_resp.json()["import_id"])

        get_resp = client.get(
            f"/admin/api/imports/{import_id}",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"
        assert get_resp.headers["X-Request-Id"] == "req_test"

        item = body["item"]
        assert item["import"]["id"] == str(import_id)
        assert item["import"]["accepted"] == 1
        assert item["job"] is not None
        assert item["job"]["id"].isdigit()


def test_admin_get_import_not_found_returns_404(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_get_import_not_found.db"
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
        resp = client.get(
            "/admin/api/imports/999",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "NOT_FOUND"
        assert body["request_id"] == "req_test"

