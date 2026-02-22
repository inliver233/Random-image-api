from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.session import create_sessionmaker


def test_models_proxy_endpoints_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm_proxy_endpoints.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            row = ProxyEndpoint(scheme="http", host="1.2.3.4", port=8080, username="u", password_enc="enc", enabled=1)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            assert row.id == 1

        async with Session() as session:
            fetched = (await session.execute(select(ProxyEndpoint))).scalars().first()
            assert fetched is not None
            assert fetched.host == "1.2.3.4"
            assert fetched.port == 8080

        await engine.dispose()

    asyncio.run(_run())

