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
from app.db.models.pixiv_tokens import PixivToken
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_token_test_refresh_success_rotates_refresh_token(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_token_test_refresh.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    field_key = Fernet.generate_key().decode("ascii")
    encryptor = FieldEncryptor.from_key(field_key)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)
    monkeypatch.setenv("PIXIV_OAUTH_CLIENT_ID", "client_id_test")
    monkeypatch.setenv("PIXIV_OAUTH_CLIENT_SECRET", "client_secret_test")
    monkeypatch.setenv("PIXIV_OAUTH_HASH_SECRET", "hash_secret_test")

    app = create_app()
    token_id: int | None = None

    refresh_token = "rt_old"
    rotated_refresh_token = "rt_new"

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            row = PixivToken(
                label="acc1",
                enabled=1,
                refresh_token_enc=encryptor.encrypt_text(refresh_token),
                refresh_token_masked="***",
                weight=1.0,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            nonlocal token_id
            token_id = int(row.id)

        await app.state.engine.dispose()

    asyncio.run(_seed())
    assert token_id is not None

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert str(req.url) == "https://oauth.secure.pixiv.net/auth/token"
        body = (req.content or b"").decode("utf-8")
        assert f"refresh_token={refresh_token}" in body
        return httpx.Response(
            200,
            json={
                "response": {
                    "access_token": "access_token_test",
                    "token_type": "bearer",
                    "expires_in": 3600,
                    "refresh_token": rotated_refresh_token,
                    "scope": "",
                    "user": {"id": 123},
                }
            },
        )

    app.state.httpx_transport = httpx.MockTransport(handler)

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            f"/admin/api/tokens/{token_id}/test-refresh",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["expires_in"] == 3600
        assert body["user_id"] == "123"
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"

        dumped = json.dumps(body, ensure_ascii=False)
        assert refresh_token not in dumped
        assert rotated_refresh_token not in dumped
        assert "access_token" not in dumped

        async def _fetch_refresh_token_enc() -> str:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT refresh_token_enc FROM pixiv_tokens WHERE id = ?",
                    (token_id,),
                )
                row = result.fetchone()
                assert row is not None
                return str(row[0])

        enc = asyncio.run(_fetch_refresh_token_enc())
        assert encryptor.decrypt_text(enc) == rotated_refresh_token


def test_admin_token_test_refresh_failure_returns_502_and_updates_backoff(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_token_test_refresh_fail.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    field_key = Fernet.generate_key().decode("ascii")
    encryptor = FieldEncryptor.from_key(field_key)

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_key)
    monkeypatch.setenv("PIXIV_OAUTH_CLIENT_ID", "client_id_test")
    monkeypatch.setenv("PIXIV_OAUTH_CLIENT_SECRET", "client_secret_test")

    app = create_app()
    token_id: int | None = None

    refresh_token = "rt_old"

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            row = PixivToken(
                label="acc1",
                enabled=1,
                refresh_token_enc=encryptor.encrypt_text(refresh_token),
                refresh_token_masked="***",
                weight=1.0,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            nonlocal token_id
            token_id = int(row.id)

        await app.state.engine.dispose()

    asyncio.run(_seed())
    assert token_id is not None

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    app.state.httpx_transport = httpx.MockTransport(handler)

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            f"/admin/api/tokens/{token_id}/test-refresh",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 502
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "TOKEN_REFRESH_FAILED"
        assert body["request_id"] == "req_test"

        dumped = json.dumps(body, ensure_ascii=False)
        assert refresh_token not in dumped

        async def _fetch_error_state() -> tuple[int, str | None]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT error_count, backoff_until FROM pixiv_tokens WHERE id = ?",
                    (token_id,),
                )
                row = result.fetchone()
                assert row is not None
                return (int(row[0]), str(row[1]) if row[1] is not None else None)

        error_count, backoff_until = asyncio.run(_fetch_error_state())
        assert error_count == 1
        assert backoff_until is not None and backoff_until

