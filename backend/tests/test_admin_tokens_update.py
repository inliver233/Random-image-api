from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.pixiv_tokens import PixivToken
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_update_token_label_enabled_weight(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_update_token.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            session.add(
                PixivToken(
                    label="old",
                    enabled=1,
                    refresh_token_enc="enc_dummy",
                    refresh_token_masked="***",
                    weight=1.0,
                )
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.put(
            "/admin/api/tokens/1",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
            json={"label": "new", "enabled": False, "weight": 2.5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["token_id"] == "1"
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"

        dumped = json.dumps(body, ensure_ascii=False)
        assert "refresh_token" not in dumped
        assert "enc_dummy" not in dumped

    async def _fetch_row() -> tuple[str | None, int, float, str]:
        async with app.state.engine.connect() as conn:
            result = await conn.exec_driver_sql(
                "SELECT label, enabled, weight, refresh_token_enc FROM pixiv_tokens WHERE id = 1;"
            )
            row = result.fetchone()
            assert row is not None
            return (row[0], int(row[1]), float(row[2]), str(row[3]))

    label, enabled, weight, refresh_token_enc = asyncio.run(_fetch_row())
    assert label == "new"
    assert enabled == 0
    assert weight == 2.5
    assert refresh_token_enc == "enc_dummy"

