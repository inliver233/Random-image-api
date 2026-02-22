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


def test_admin_clear_binding_override(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_clear_binding_override.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    pool_id: int | None = None
    binding_id: int | None = None
    p1_id: int | None = None

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

            session.add_all(
                [
                    ProxyPoolEndpoint(pool_id=int(pool.id), endpoint_id=int(p1.id), enabled=1, weight=1),
                    ProxyPoolEndpoint(pool_id=int(pool.id), endpoint_id=int(p2.id), enabled=1, weight=1),
                ]
            )
            await session.commit()

            binding = TokenProxyBinding(
                token_id=int(token.id),
                pool_id=int(pool.id),
                primary_proxy_id=int(p1.id),
                override_proxy_id=int(p2.id),
                override_expires_at="2099-01-01T00:00:00.000Z",
            )
            session.add(binding)
            await session.commit()
            await session.refresh(binding)

            nonlocal pool_id, binding_id, p1_id
            pool_id = int(pool.id)
            binding_id = int(binding.id)
            p1_id = int(p1.id)

    asyncio.run(_seed())
    assert pool_id is not None
    assert binding_id is not None
    assert p1_id is not None

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        clear_resp = client.post(
            f"/admin/api/bindings/{binding_id}/clear-override",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert clear_resp.status_code == 200
        body1 = clear_resp.json()
        assert body1["ok"] is True
        assert body1["binding_id"] == str(binding_id)
        assert body1["request_id"] == "req_test"
        assert clear_resp.headers["X-Request-Id"] == "req_test"

        list_resp = client.get(
            "/admin/api/bindings",
            params={"pool_id": pool_id},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert list_resp.status_code == 200
        body2 = list_resp.json()
        assert body2["ok"] is True
        assert len(body2["items"]) == 1
        item = body2["items"][0]
        assert item["override_proxy"] is None
        assert item["effective_mode"] == "primary"
        assert item["effective_proxy_id"] == str(p1_id)

