from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.pixiv_tokens import PixivToken
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.models.proxy_pool_endpoints import ProxyPoolEndpoint
from app.db.models.proxy_pools import ProxyPool
from app.db.models.token_proxy_bindings import TokenProxyBinding
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_delete_token_removes_bindings(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_delete_token.db"
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
            token = PixivToken(
                label="t1",
                enabled=1,
                refresh_token_enc="enc_dummy",
                refresh_token_masked="***",
                weight=1.0,
            )
            session.add(token)

            endpoint = ProxyEndpoint(
                scheme="http",
                host="127.0.0.1",
                port=8080,
                username="u",
                password_enc="",
                enabled=1,
                source="manual",
                source_ref=None,
            )
            session.add(endpoint)

            pool = ProxyPool(name="pool1", description=None, enabled=1)
            session.add(pool)
            await session.flush()

            session.add(ProxyPoolEndpoint(pool_id=int(pool.id), endpoint_id=int(endpoint.id), enabled=1, weight=1))
            session.add(
                TokenProxyBinding(
                    token_id=int(token.id),
                    pool_id=int(pool.id),
                    primary_proxy_id=int(endpoint.id),
                    override_proxy_id=None,
                    override_expires_at=None,
                )
            )
            await session.commit()

        await app.state.engine.dispose()

    asyncio.run(_seed())

    admin_token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.delete(
            "/admin/api/tokens/1",
            headers={"Authorization": f"Bearer {admin_token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["token_id"] == "1"
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"

    async def _counts() -> tuple[int, int]:
        async with app.state.engine.connect() as conn:
            t_count = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM pixiv_tokens;")).scalar_one())
            b_count = int((await conn.exec_driver_sql("SELECT COUNT(*) FROM token_proxy_bindings;")).scalar_one())
            return t_count, b_count

    token_count, binding_count = asyncio.run(_counts())
    assert token_count == 0
    assert binding_count == 0

