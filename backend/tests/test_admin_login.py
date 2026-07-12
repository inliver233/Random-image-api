from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.models.base import Base
from app.main import create_app


def test_admin_login_happy_path_returns_token(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_login.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"
        assert isinstance(body["token"], str) and body["token"]
        assert "password" not in body


def test_admin_login_invalid_credentials_returns_401(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_login_invalid.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "wrong"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "UNAUTHORIZED"
        assert body["request_id"] == "req_test"


def test_admin_logout_happy_path(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_logout.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    with TestClient(app) as client:
        token = client.post(
            "/admin/api/login",
            headers={"X-Request-Id": "req_test"},
            json={"username": "admin", "password": "pass_test"},
        ).json()["token"]

        resp = client.post(
            "/admin/api/logout",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"


def test_admin_logout_missing_token_returns_401(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_logout_missing.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pass_test")

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    with TestClient(app) as client:
        resp = client.post("/admin/api/logout", headers={"X-Request-Id": "req_test"})
        assert resp.status_code == 401
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "UNAUTHORIZED"
        assert body["request_id"] == "req_test"


def test_admin_auth_openapi_documents_jwt_vs_public_key() -> None:
    """Login/logout OpenAPI must document admin JWT vs public API keys."""
    app = create_app()
    paths = app.openapi()["paths"]

    login_op = paths["/admin/api/login"]["post"]
    assert login_op.get("summary") == "Admin login"
    desc = str(login_op.get("description") or "")
    assert "JWT" in desc or "jwt" in desc.lower()
    assert "API-Key" in desc or "public" in desc.lower()

    logout_op = paths["/admin/api/logout"]["post"]
    assert logout_op.get("summary") == "Admin logout"
    assert "stateless" in str(logout_op.get("description") or "").lower() or "JWT" in str(
        logout_op.get("description") or ""
    )
