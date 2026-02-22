from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.imports import Import
from app.db.session import create_sessionmaker


def test_models_imports_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            row = Import(created_by="tester", source="manual", detail_json="{}")
            session.add(row)
            await session.commit()
            await session.refresh(row)
            assert row.id == 1

        async with Session() as session:
            fetched = (await session.execute(select(Import))).scalars().first()
            assert fetched is not None
            assert fetched.id == 1
            assert fetched.created_by == "tester"
            assert fetched.source == "manual"

        await engine.dispose()

    asyncio.run(_run())

