from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.proxy_pools import ProxyPool
from app.db.session import create_sessionmaker


def test_models_proxy_pools_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm_proxy_pools.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            row = ProxyPool(name="pool1", description="desc", enabled=1)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            assert row.id == 1

        async with Session() as session:
            fetched = (await session.execute(select(ProxyPool))).scalars().first()
            assert fetched is not None
            assert fetched.name == "pool1"

        await engine.dispose()

    asyncio.run(_run())

