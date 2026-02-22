from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.pixiv_tokens import PixivToken
from app.db.session import create_sessionmaker


def test_models_pixiv_tokens_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm_pixiv_tokens.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            row = PixivToken(
                label="acc1",
                enabled=1,
                refresh_token_enc="enc",
                refresh_token_masked="***",
                weight=1.0,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            assert row.id == 1

        async with Session() as session:
            fetched = (await session.execute(select(PixivToken))).scalars().first()
            assert fetched is not None
            assert fetched.label == "acc1"
            assert fetched.refresh_token_enc == "enc"
            assert fetched.refresh_token_masked == "***"

        await engine.dispose()

    asyncio.run(_run())

