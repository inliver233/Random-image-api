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


def test_admin_import_proxies_encrypts_password_and_supports_overwrite(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_import_proxies.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    field_key = Fernet.generate_key().decode("ascii")
    encryptor = FieldEncryptor.from_key(field_key)

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

    password1 = "pass_SECRET_1"
    password2 = "pass_SECRET_2"

    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/proxies/endpoints/import",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={
                "text": "\n".join(
                    [
                        f"http://u:{password1}@1.2.3.4:8080",
                        "socks5://5.6.7.8:1080",
                        "not_a_proxy",
                    ]
                ),
                "source": "manual",
                "conflict_policy": "skip",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["created"] == 2
        assert body["updated"] == 0
        assert body["skipped"] == 0
        assert len(body["errors"]) == 1

        dumped = json.dumps(body, ensure_ascii=False)
        assert password1 not in dumped
        assert "password" not in dumped

        async def _fetch_passwords() -> list[str]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT scheme, host, port, username, password_enc FROM proxy_endpoints ORDER BY id ASC"
                )
                rows = result.fetchall()
                assert rows is not None
                out: list[str] = []
                for scheme, host, port, username, password_enc in rows:
                    _ = scheme, host, port, username
                    out.append(str(password_enc))
                return out

        encs = asyncio.run(_fetch_passwords())
        assert len(encs) == 2
        assert encryptor.decrypt_text(encs[0]) == password1
        assert encs[1] == ""

        resp2 = client.post(
            "/admin/api/proxies/endpoints/import",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={
                "text": f"http://u:{password2}@1.2.3.4:8080",
                "source": "manual",
                "conflict_policy": "overwrite",
            },
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["created"] == 0
        assert body2["updated"] == 1

        async def _fetch_first_enc() -> str:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT password_enc FROM proxy_endpoints WHERE host = ? AND port = ? AND username = ?",
                    ("1.2.3.4", 8080, "u"),
                )
                row = result.fetchone()
                assert row is not None
                return str(row[0])

        enc = asyncio.run(_fetch_first_enc())
        assert encryptor.decrypt_text(enc) == password2


def test_admin_import_proxies_auto_generates_field_encryption_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    db_path = tmp_path / "admin_import_proxies_auto.db"
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

    password = "pass_SECRET_1"
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/proxies/endpoints/import",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={
                "text": f"http://u:{password}@1.2.3.4:8080",
                "source": "manual",
                "conflict_policy": "skip",
            },
        )
        assert resp.status_code == 200

        async def _fetch_first_enc() -> str:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT password_enc FROM proxy_endpoints WHERE host = ? AND port = ? AND username = ?",
                    ("1.2.3.4", 8080, "u"),
                )
                row = result.fetchone()
                assert row is not None
                return str(row[0])

        enc = asyncio.run(_fetch_first_enc())
        assert encryptor.decrypt_text(enc) == password
