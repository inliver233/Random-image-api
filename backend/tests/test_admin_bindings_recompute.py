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
from app.db.session import create_sessionmaker
from app.main import create_app


def test_admin_recompute_bindings_creates_rows_and_respects_capacity(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_recompute_bindings.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    token_ids: list[int] = []
    pool_id: int | None = None

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
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
            t1 = PixivToken(
                label="t1",
                enabled=1,
                refresh_token_enc="enc_dummy",
                refresh_token_masked="***",
                weight=1.0,
            )
            t2 = PixivToken(
                label="t2",
                enabled=1,
                refresh_token_enc="enc_dummy",
                refresh_token_masked="***",
                weight=1.0,
            )
            t3 = PixivToken(
                label="t3",
                enabled=1,
                refresh_token_enc="enc_dummy",
                refresh_token_masked="***",
                weight=1.0,
            )
            session.add_all([pool, p1, p2, t1, t2, t3])
            await session.commit()
            await session.refresh(pool)
            await session.refresh(p1)
            await session.refresh(p2)
            await session.refresh(t1)
            await session.refresh(t2)
            await session.refresh(t3)

            session.add_all(
                [
                    ProxyPoolEndpoint(pool_id=int(pool.id), endpoint_id=int(p1.id), enabled=1, weight=1),
                    ProxyPoolEndpoint(pool_id=int(pool.id), endpoint_id=int(p2.id), enabled=1, weight=1),
                ]
            )
            await session.commit()

            nonlocal pool_id
            pool_id = int(pool.id)

            nonlocal token_ids
            token_ids = [int(t1.id), int(t2.id), int(t3.id)]

    asyncio.run(_seed())
    assert pool_id is not None

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        recompute_resp = client.post(
            "/admin/api/bindings/recompute",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"pool_id": pool_id, "max_tokens_per_proxy": 2},
        )
        assert recompute_resp.status_code == 200
        body1 = recompute_resp.json()
        assert body1["ok"] is True
        assert body1["pool_id"] == str(pool_id)
        assert body1["recomputed"] == 3
        assert body1["request_id"] == "req_test"
        assert recompute_resp.headers["X-Request-Id"] == "req_test"

        list_resp = client.get(
            "/admin/api/bindings",
            params={"pool_id": pool_id},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert list_resp.status_code == 200
        body2 = list_resp.json()
        assert body2["ok"] is True
        assert body2["request_id"] == "req_test"
        assert list_resp.headers["X-Request-Id"] == "req_test"
        assert len(body2["items"]) == 3

        seen_tokens = {int(item["token"]["id"]) for item in body2["items"]}
        assert seen_tokens == set(token_ids)

        by_proxy: dict[str, int] = {}
        for item in body2["items"]:
            assert item["effective_mode"] == "primary"
            by_proxy[item["primary_proxy"]["id"]] = by_proxy.get(item["primary_proxy"]["id"], 0) + 1
        assert all(count <= 2 for count in by_proxy.values())


def test_admin_recompute_bindings_rejects_insufficient_capacity(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_recompute_bindings_capacity.db"
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
            tokens = [
                PixivToken(
                    label=f"t{i}",
                    enabled=1,
                    refresh_token_enc="enc_dummy",
                    refresh_token_masked="***",
                    weight=1.0,
                )
                for i in range(5)
            ]
            session.add_all([pool, p1, p2, *tokens])
            await session.commit()
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

            nonlocal pool_id
            pool_id = int(pool.id)

    asyncio.run(_seed())
    assert pool_id is not None

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/bindings/recompute",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"pool_id": pool_id, "max_tokens_per_proxy": 2},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "BAD_REQUEST"
        assert body["request_id"] == "req_test"


def test_admin_recompute_bindings_respects_pool_endpoint_weight(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_recompute_bindings_weight.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()

    pool_id: int | None = None
    endpoint_id: int | None = None

    async def _seed() -> None:
        async with app.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(app.state.engine)
        async with Session() as session:
            pool = ProxyPool(name="pool_weight", description=None, enabled=1)
            p1 = ProxyEndpoint(
                scheme="http",
                host="1.2.3.4",
                port=8080,
                username="",
                password_enc="",
                enabled=1,
                source="manual",
            )
            tokens = [
                PixivToken(
                    label=f"t{i}",
                    enabled=1,
                    refresh_token_enc="enc_dummy",
                    refresh_token_masked="***",
                    weight=1.0,
                )
                for i in range(5)
            ]
            session.add_all([pool, p1, *tokens])
            await session.commit()
            await session.refresh(pool)
            await session.refresh(p1)

            session.add(ProxyPoolEndpoint(pool_id=int(pool.id), endpoint_id=int(p1.id), enabled=1, weight=5))
            await session.commit()

            nonlocal pool_id, endpoint_id
            pool_id = int(pool.id)
            endpoint_id = int(p1.id)

    asyncio.run(_seed())
    assert pool_id is not None
    assert endpoint_id is not None

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/bindings/recompute",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"pool_id": pool_id, "max_tokens_per_proxy": 1},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["pool_id"] == str(pool_id)
        assert body["recomputed"] == 5

        list_resp = client.get(
            "/admin/api/bindings",
            params={"pool_id": pool_id},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        assert len(items) == 5
        assert {int(it["primary_proxy"]["id"]) for it in items} == {int(endpoint_id)}


def test_admin_recompute_bindings_allows_over_capacity_when_strict_false(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "admin_recompute_bindings_non_strict.db"
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
            tokens = [
                PixivToken(
                    label=f"t{i}",
                    enabled=1,
                    refresh_token_enc="enc_dummy",
                    refresh_token_masked="***",
                    weight=1.0,
                )
                for i in range(5)
            ]
            session.add_all([pool, p1, p2, *tokens])
            await session.commit()
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

            nonlocal pool_id
            pool_id = int(pool.id)

    asyncio.run(_seed())
    assert pool_id is not None

    token = create_jwt(secret_key="secret_test", subject="admin", ttl_s=3600)
    with TestClient(app) as client:
        resp = client.post(
            "/admin/api/bindings/recompute",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
            json={"pool_id": pool_id, "max_tokens_per_proxy": 2, "strict": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["pool_id"] == str(pool_id)
        assert body["recomputed"] == 5
        assert body["strict"] is False
        assert int(body["capacity"]) == 4
        assert int(body["over_capacity_assigned"]) == 1

        list_resp = client.get(
            "/admin/api/bindings",
            params={"pool_id": pool_id},
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "req_test"},
        )
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        assert len(items) == 5
