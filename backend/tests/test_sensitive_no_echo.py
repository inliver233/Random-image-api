from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_sensitive_refresh_token_not_in_logs_or_response(tmp_path: Path, monkeypatch, caplog) -> None:
    db_path = tmp_path / "sensitive_token_create.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    field_key = Fernet.generate_key().decode("ascii")

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    refresh_token = "rt_sensitive_secret_123"
    caplog.set_level(logging.INFO)

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/tokens",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={"label": "acc1", "refresh_token": refresh_token, "enabled": True, "weight": 1.0},
        )
        assert resp.status_code == 200

        dumped = json.dumps(resp.json(), ensure_ascii=False)
        assert refresh_token not in dumped

    assert refresh_token not in caplog.text


def test_sensitive_proxy_password_not_in_logs_or_response(tmp_path: Path, monkeypatch, caplog) -> None:
    db_path = tmp_path / "sensitive_proxy_import.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    field_key = Fernet.generate_key().decode("ascii")

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    password = "proxy_pass_sensitive_123"
    import_text = "\n".join([f"http://u:{password}@1.2.3.4:8080", "socks5://5.6.7.8:1080"])

    caplog.set_level(logging.INFO)

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/proxies/endpoints/import",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={"text": import_text, "source": "manual", "conflict_policy": "skip"},
        )
        assert resp.status_code == 200

        dumped = json.dumps(resp.json(), ensure_ascii=False)
        assert password not in dumped

    assert password not in caplog.text
    assert import_text not in caplog.text


def test_sensitive_easy_proxies_password_not_in_logs_or_response(tmp_path: Path, monkeypatch, caplog) -> None:
    db_path = tmp_path / "sensitive_easy_import.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    field_key = Fernet.generate_key().decode("ascii")

    base_url = "http://easy-proxies.test:9090"
    easy_password = "easy_admin_password_secret_sensitive"
    easy_proxy_pass = "easy_proxy_pass_secret_sensitive"

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == f"{base_url}/api/auth":
            data = json.loads((req.content or b"{}").decode("utf-8"))
            assert data["password"] == easy_password
            return httpx.Response(200, json={"token": "t"})
        if str(req.url) == f"{base_url}/api/export":
            return httpx.Response(200, text=f"http://u:{easy_proxy_pass}@1.2.3.4:8080\n")
        return httpx.Response(404)

    app.state.httpx_transport = httpx.MockTransport(handler)

    caplog.set_level(logging.INFO)

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/proxies/easy-proxies/import",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={"base_url": base_url, "password": easy_password, "conflict_policy": "skip_non_easy_proxies"},
        )
        assert resp.status_code == 200

        dumped = json.dumps(resp.json(), ensure_ascii=False)
        assert easy_password not in dumped
        assert easy_proxy_pass not in dumped

    assert easy_password not in caplog.text
    assert easy_proxy_pass not in caplog.text

