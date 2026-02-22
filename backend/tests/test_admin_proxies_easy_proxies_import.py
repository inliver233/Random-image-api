from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.crypto import FieldEncryptor
from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_easy_proxies_import_skip_non_easy_proxies(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_easy_proxies_import.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    field_key = Fernet.generate_key().decode("ascii")
    encryptor = FieldEncryptor.from_key(field_key)

    base_url = "http://easy-proxies.test:9090"
    easy_password = "easy_admin_password_secret"
    easy_proxy_pass = "easy_proxy_pass_secret"
    manual_pass = "manual_pass_secret"

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)

    app = create_app()

    async def _seed_manual() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                ProxyEndpoint(
                    scheme="http",
                    host="1.2.3.4",
                    port=8080,
                    username="u",
                    password_enc=encryptor.encrypt_text(manual_pass),
                    enabled=1,
                    source="manual",
                    source_ref=None,
                )
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed_manual())

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == f"{base_url}/api/auth":
            data = json.loads((req.content or b"{}").decode("utf-8"))
            assert data["password"] == easy_password
            return httpx.Response(200, json={"token": "t"})
        if str(req.url) == f"{base_url}/api/export":
            assert req.headers.get("Authorization") == "Bearer t"
            return httpx.Response(
                200,
                text="\n".join(
                    [
                        f"http://u:{easy_proxy_pass}@1.2.3.4:8080",
                        "socks5://5.6.7.8:1080",
                        "",
                    ]
                ),
            )
        return httpx.Response(404)

    app.state.httpx_transport = httpx.MockTransport(handler)

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/proxies/easy-proxies/import",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={"base_url": base_url, "password": easy_password, "conflict_policy": "skip_non_easy_proxies"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["created"] == 1
        assert body["updated"] == 0
        assert body["skipped"] == 1

        dumped = json.dumps(body, ensure_ascii=False)
        assert easy_password not in dumped
        assert easy_proxy_pass not in dumped
        assert "password" not in dumped

        async def _fetch_password_and_source(host: str, port: int, username: str) -> tuple[str, str, str | None]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT password_enc, source, source_ref FROM proxy_endpoints WHERE host = ? AND port = ? AND username = ?",
                    (host, port, username),
                )
                row = result.fetchone()
                assert row is not None
                return (str(row[0]), str(row[1]), str(row[2]) if row[2] is not None else None)

        enc_manual, source_manual, source_ref_manual = asyncio.run(_fetch_password_and_source("1.2.3.4", 8080, "u"))
        assert source_manual == "manual"
        assert source_ref_manual is None
        assert encryptor.decrypt_text(enc_manual) == manual_pass

        enc_new, source_new, source_ref_new = asyncio.run(_fetch_password_and_source("5.6.7.8", 1080, ""))
        assert source_new == "easy_proxies"
        assert source_ref_new == base_url
        assert enc_new == ""


def test_admin_easy_proxies_import_allows_missing_password(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_easy_proxies_import_no_password.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    field_key = Fernet.generate_key().decode("ascii")

    base_url = "http://easy-proxies.test:9090"

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
            raise AssertionError("auth should not be called when password is missing")
        if str(req.url) == f"{base_url}/api/export":
            assert req.headers.get("Authorization") is None
            return httpx.Response(200, text="http://1.2.3.4:8080\n")
        return httpx.Response(404)

    app.state.httpx_transport = httpx.MockTransport(handler)

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/proxies/easy-proxies/import",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={"base_url": base_url, "conflict_policy": "overwrite"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["created"] == 1
        assert body["updated"] == 0
        assert body["skipped"] == 0

        dumped = json.dumps(body, ensure_ascii=False)
        assert "password" not in dumped


def test_admin_easy_proxies_import_clears_blacklist_on_update(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_easy_proxies_import_clear_blacklist.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    field_key = Fernet.generate_key().decode("ascii")

    base_url = "http://easy-proxies.test:9090"

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                ProxyEndpoint(
                    scheme="socks5",
                    host="5.6.7.8",
                    port=1080,
                    username="",
                    password_enc="",
                    enabled=1,
                    source="easy_proxies",
                    source_ref=base_url,
                    failure_count=3,
                    blacklisted_until="2099-01-01T00:00:00Z",
                    last_error="ConnectError: old",
                )
            )
            await session.commit()

    asyncio.run(_seed())

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == f"{base_url}/api/auth":
            return httpx.Response(200, json={"token": "t"})
        if str(req.url) == f"{base_url}/api/export":
            assert req.headers.get("Authorization") == "Bearer t"
            return httpx.Response(200, text="socks5://5.6.7.8:1080\n")
        return httpx.Response(404)

    app.state.httpx_transport = httpx.MockTransport(handler)

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/proxies/easy-proxies/import",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={"base_url": base_url, "password": "pw", "conflict_policy": "overwrite"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["created"] == 0
        assert body["updated"] == 1
        assert body["skipped"] == 0

        async def _fetch_flags() -> tuple[int, str | None, str | None]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT failure_count, blacklisted_until, last_error FROM proxy_endpoints WHERE scheme='socks5' AND host='5.6.7.8' AND port=1080",
                )
                row = result.fetchone()
                assert row is not None
                return int(row[0] or 0), str(row[1]) if row[1] is not None else None, str(row[2]) if row[2] is not None else None

        failure_count, blacklisted_until, last_error = asyncio.run(_fetch_flags())
        assert failure_count == 3
        assert blacklisted_until is None
        assert last_error is None
