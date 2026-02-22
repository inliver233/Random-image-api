from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.models.proxy_pool_endpoints import ProxyPoolEndpoint
from app.db.models.proxy_pools import ProxyPool
from app.db.session import create_sessionmaker


def test_models_proxy_pool_endpoints_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm_proxy_pool_endpoints.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            pool = ProxyPool(name="pool1", enabled=1)
            session.add(pool)

            endpoint = ProxyEndpoint(scheme="http", host="1.2.3.4", port=8080, username="u", password_enc="enc", enabled=1)
            session.add(endpoint)

            await session.flush()
            session.add(ProxyPoolEndpoint(pool_id=pool.id, endpoint_id=endpoint.id, enabled=1, weight=1))
            await session.commit()

        async with Session() as session:
            row = (await session.execute(select(ProxyPoolEndpoint))).scalars().first()
            assert row is not None
            assert row.pool_id == 1
            assert row.endpoint_id == 1

        await engine.dispose()

    asyncio.run(_run())

