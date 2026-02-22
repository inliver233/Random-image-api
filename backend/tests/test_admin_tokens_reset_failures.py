from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.pixiv_tokens import PixivToken
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_token_reset_failures_clears_backoff_and_error_state(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_token_reset_failures.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()
    token_id: int | None = None

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            row = PixivToken(
                label="acc1",
                enabled=1,
                refresh_token_enc="enc_dummy",
                refresh_token_masked="***",
                weight=1.0,
                error_count=2,
                backoff_until="2099-01-01T00:00:00Z",
                last_fail_at="2099-01-01T00:00:00Z",
                last_error_code="TOKEN_REFRESH_FAILED",
                last_error_msg="oops",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            nonlocal token_id
            token_id = int(row.id)

        await app.state.engine.dispose()

    asyncio.run(_seed())
    assert token_id is not None

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            f"/admin/api/tokens/{token_id}/reset-failures",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"

        async def _fetch_state() -> tuple[int, str | None, str | None, str | None]:
            async with app.state.engine.connect() as conn:
                result = await conn.exec_driver_sql(
                    "SELECT error_count, backoff_until, last_error_code, last_error_msg FROM pixiv_tokens WHERE id = ?",
                    (token_id,),
                )
                row = result.fetchone()
                assert row is not None
                return (
                    int(row[0]),
                    str(row[1]) if row[1] is not None else None,
                    str(row[2]) if row[2] is not None else None,
                    str(row[3]) if row[3] is not None else None,
                )

        error_count, backoff_until, last_error_code, last_error_msg = asyncio.run(_fetch_state())
        assert error_count == 0
        assert backoff_until is None
        assert last_error_code is None
        assert last_error_msg is None

