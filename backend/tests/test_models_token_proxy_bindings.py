from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.pixiv_tokens import PixivToken
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.models.proxy_pools import ProxyPool
from app.db.models.token_proxy_bindings import TokenProxyBinding
from app.db.session import create_sessionmaker


def test_models_token_proxy_bindings_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm_token_proxy_bindings.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            token = PixivToken(refresh_token_enc="enc", refresh_token_masked="***", enabled=1, weight=1.0)
            pool = ProxyPool(name="pool1", enabled=1)
            ep1 = ProxyEndpoint(scheme="http", host="1.2.3.4", port=8080, username="u", password_enc="enc", enabled=1)
            ep2 = ProxyEndpoint(scheme="http", host="1.2.3.5", port=8080, username="u", password_enc="enc", enabled=1)
            session.add_all([token, pool, ep1, ep2])
            await session.flush()

            binding = TokenProxyBinding(
                token_id=token.id,
                pool_id=pool.id,
                primary_proxy_id=ep1.id,
                override_proxy_id=ep2.id,
                override_expires_at="2026-02-10T00:00:00.000Z",
            )
            session.add(binding)
            await session.commit()
            await session.refresh(binding)
            assert binding.id == 1

        async with Session() as session:
            fetched = (await session.execute(select(TokenProxyBinding))).scalars().first()
            assert fetched is not None
            assert fetched.token_id == 1
            assert fetched.pool_id == 1
            assert fetched.primary_proxy_id == 1

        await engine.dispose()

    asyncio.run(_run())

