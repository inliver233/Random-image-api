from __future__ import annotations

import asyncio
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.security import create_jwt
from app.db.models.base import Base
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.models.proxy_pools import ProxyPool
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_set_proxy_pool_endpoints_replaces_membership(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_set_proxy_pool_endpoints.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()
    pool_id: int | None = None
    ep1_id: int | None = None
    ep2_id: int | None = None

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            pool = ProxyPool(name="pool1", description=None, enabled=1)
            session.add(pool)
            session.add_all(
                [
                    ProxyEndpoint(
                        scheme="http",
                        host="1.2.3.4",
                        port=8080,
                        username="",
                        password_enc="",
                        enabled=1,
                        source="manual",
                    ),
                    ProxyEndpoint(
                        scheme="socks5",
                        host="5.6.7.8",
                        port=1080,
                        username="",
                        password_enc="",
                        enabled=1,
                        source="manual",
                    ),
                ]
            )
            await session.commit()
            await session.refresh(pool)
            nonlocal pool_id, ep1_id, ep2_id
            pool_id = int(pool.id)

            eps = (
                (
                    await session.execute(
                        sa.select(ProxyEndpoint).order_by(ProxyEndpoint.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            ep1_id = int(eps[0].id)
            ep2_id = int(eps[1].id)

        await app.state.engine.dispose()

    asyncio.run(_seed())
    assert pool_id is not None and ep1_id is not None and ep2_id is not None

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp1 = client.post(
            f"/admin/api/proxy-pools/{pool_id}/endpoints",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"items": [{"endpoint_id": ep1_id, "enabled": True, "weight": 2}]},
        )
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert body1["created"] == 1
        assert body1["removed"] == 0

        resp2 = client.post(
            f"/admin/api/proxy-pools/{pool_id}/endpoints",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={
                "items": [
                    {"endpoint_id": ep1_id, "enabled": False, "weight": 1},
                    {"endpoint_id": ep2_id, "enabled": True, "weight": 3},
                ]
            },
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["created"] == 1
        assert body2["updated"] == 1
        assert body2["removed"] == 0

        resp3 = client.post(
            f"/admin/api/proxy-pools/{pool_id}/endpoints",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"items": []},
        )
        assert resp3.status_code == 200
        assert resp3.json()["removed"] == 2


def test_admin_set_proxy_pool_endpoints_unknown_endpoint_returns_400(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_set_proxy_pool_endpoints_unknown.db"
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
            pool = ProxyPool(name="pool1", description=None, enabled=1)
            session.add(pool)
            await session.commit()
            await session.refresh(pool)
            nonlocal pool_id
            pool_id = int(pool.id)

        await app.state.engine.dispose()

    asyncio.run(_seed())
    assert pool_id is not None

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            f"/admin/api/proxy-pools/{pool_id}/endpoints",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"items": [{"endpoint_id": 999, "enabled": True, "weight": 1}]},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"
