from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.pixiv_tokens import PixivToken
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.models.proxy_pools import ProxyPool
from app.db.models.token_proxy_bindings import TokenProxyBinding
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_list_bindings_filters_by_pool_id(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_list_bindings.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()
    pool_id: int | None = None

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            token = PixivToken(
                label="acc1",
                enabled=1,
                refresh_token_enc="enc_dummy",
                refresh_token_masked="***",
                weight=1.0,
            )
            pool = ProxyPool(name="pool1", description=None, enabled=1)
            p1 = ProxyEndpoint(
                scheme="http",
                host="1.2.3.4",
                port=8080,
                username="",
                password_enc="",
                enabled=1,
                source="manual",
            )
            p2 = ProxyEndpoint(
                scheme="socks5",
                host="5.6.7.8",
                port=1080,
                username="",
                password_enc="",
                enabled=1,
                source="manual",
            )
            session.add_all([token, pool, p1, p2])
            await session.commit()
            await session.refresh(token)
            await session.refresh(pool)
            await session.refresh(p1)
            await session.refresh(p2)

            session.add(
                TokenProxyBinding(
                    token_id=int(token.id),
                    pool_id=int(pool.id),
                    primary_proxy_id=int(p1.id),
                    override_proxy_id=int(p2.id),
                    override_expires_at="2099-01-01T00:00:00Z",
                )
            )
            await session.commit()

            nonlocal pool_id
            pool_id = int(pool.id)

        await app.state.engine.dispose()

    asyncio.run(_seed())
    assert pool_id is not None

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.get(
            "/admin/api/bindings",
            params={"pool_id": pool_id},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["request_id"] == "req_test"
        assert resp.headers["X-Request-Id"] == "req_test"
        assert len(body["items"]) == 1
        assert body["items"][0]["pool"]["id"] == str(pool_id)
        assert body["items"][0]["effective_mode"] == "override"

