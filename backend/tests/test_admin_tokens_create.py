from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.crypto import FieldEncryptor
from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_admin_create_token_encrypts_and_does_not_echo_refresh_token(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_create_token.db"
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

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        refresh_token = "rt_secret_123"
        resp = client.post(
            "/admin/api/tokens",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={"label": "acc1", "refresh_token": refresh_token, "enabled": True, "weight": 1.0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"
        assert body["token_id"].isdigit()

        dumped = json.dumps(body, ensure_ascii=False)
        assert refresh_token not in dumped
        assert "refresh_token" not in dumped

        token_id = int(body["token_id"])

        async def _fetch_row() -> tuple[str, str]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT refresh_token_enc, refresh_token_masked FROM pixiv_tokens WHERE id = ?",
                    (token_id,),
                )
                row = result.fetchone()
                assert row is not None
                return (str(row[0]), str(row[1]))

        enc, masked = asyncio.run(_fetch_row())
        assert masked == "***"
        assert enc != refresh_token
        assert FieldEncryptor.from_key(field_key).decrypt_text(enc) == refresh_token


def test_admin_create_token_auto_generates_field_encryption_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    db_path = tmp_path / "admin_create_token_auto.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY_FILE", raising=False)

    app = create_app()

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    key_path = tmp_path / "data" / "field_encryption_key"
    assert key_path.exists()
    field_key = key_path.read_text(encoding="utf-8").strip()
    encryptor = FieldEncryptor.from_key(field_key)

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        refresh_token = "rt_secret_123"
        resp = client.post(
            "/admin/api/tokens",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={"label": "acc1", "refresh_token": refresh_token, "enabled": True, "weight": 1.0},
        )
        assert resp.status_code == 200

        body = resp.json()
        token_id = int(body["token_id"])

        async def _fetch_enc() -> str:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT refresh_token_enc FROM pixiv_tokens WHERE id = ?",
                    (token_id,),
                )
                row = result.fetchone()
                assert row is not None
                return str(row[0])

        enc = asyncio.run(_fetch_enc())
        assert encryptor.decrypt_text(enc) == refresh_token
