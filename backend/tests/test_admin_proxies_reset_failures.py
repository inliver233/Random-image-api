from __future__ import annotations

import asyncio
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_proxy_reset_failures_clears_blacklist(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_proxy_reset_failures.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))

    app = create_app()

    async def _seed() -> int:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            row = ProxyEndpoint(
                scheme="http",
                host="1.2.3.4",
                port=8080,
                username="u",
                password_enc="",
                enabled=1,
                source="manual",
                source_ref=None,
                failure_count=9,
                blacklisted_until="2099-01-01T00:00:00Z",
                last_fail_at="2099-01-01T00:00:00Z",
                last_error="ConnectError: old",
            )
            session.add(row)
            await session.commit()
            return int(row.id)

    endpoint_id = asyncio.run(_seed())

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            f"/admin/api/proxies/endpoints/{endpoint_id}/reset-failures",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["endpoint_id"] == str(endpoint_id)

    async def _fetch() -> tuple[int, str | None, str | None, str | None]:
        async with app.state.engine.connect() as conn:
            result = await conn.exec_driver_sql(
                "SELECT failure_count, blacklisted_until, last_fail_at, last_error FROM proxy_endpoints WHERE id = ?",
                (int(endpoint_id),),
            )
            row = result.fetchone()
            assert row is not None
            return (
                int(row[0] or 0),
                str(row[1]) if row[1] is not None else None,
                str(row[2]) if row[2] is not None else None,
                str(row[3]) if row[3] is not None else None,
            )

    failure_count, blacklisted_until, last_fail_at, last_error = asyncio.run(_fetch())
    assert failure_count == 0
    assert blacklisted_until is None
    assert last_fail_at is None
    assert last_error is None

