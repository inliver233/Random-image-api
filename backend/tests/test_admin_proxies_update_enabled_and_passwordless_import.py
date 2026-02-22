from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.main import create_app


def test_admin_update_proxy_endpoint_enabled(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_update_proxy_enabled.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with app.state.engine.begin() as conn:
            await conn.exec_driver_sql(
                "INSERT INTO proxy_endpoints (scheme, host, port, username, password_enc, enabled, source) VALUES (?,?,?,?,?,?,?)",
                ("http", "1.2.3.4", 8080, "", "", 1, "manual"),
            )

        await app.state.engine.dispose()

    asyncio.run(_seed())

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.put(
            "/admin/api/proxies/endpoints/1",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={"enabled": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["endpoint_id"] == "1"
        assert body["enabled"] is False
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"

    async def _fetch_enabled() -> int:
        async with app.state.engine.connect() as conn:
            result = await conn.exec_driver_sql("SELECT enabled FROM proxy_endpoints WHERE id = 1")
            row = result.fetchone()
            assert row is not None
            return int(row[0])

    assert asyncio.run(_fetch_enabled()) == 0


def test_admin_import_passwordless_proxies_without_encryption_key(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_import_proxy_no_enc_key.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    app.state.settings = replace(app.state.settings, field_encryption_key="")

    async def _migrate() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_migrate())

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/proxies/endpoints/import",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={
                "text": "\n".join(
                    [
                        "http://1.2.3.4:8080",
                        "socks5://5.6.7.8:1080",
                        "",
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
        assert body["errors"] == []
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"

        resp2 = client.post(
            "/admin/api/proxies/endpoints/import",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={
                "text": "http://u:pass_SECRET_1@9.9.9.9:8080",
                "source": "manual",
                "conflict_policy": "skip",
            },
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["ok"] is True
        assert body2["created"] == 0
        assert body2["updated"] == 0
        assert body2["skipped"] == 0
        assert len(body2["errors"]) == 1
        assert body2["errors"][0]["code"] == "encryption_not_configured"

        dumped = json.dumps(body2, ensure_ascii=False)
        assert "pass_SECRET_1" not in dumped

    async def _count_endpoints() -> int:
        async with app.state.engine.connect() as conn:
            result = await conn.exec_driver_sql("SELECT COUNT(*) FROM proxy_endpoints")
            return int(result.scalar_one())

    assert asyncio.run(_count_endpoints()) == 2
