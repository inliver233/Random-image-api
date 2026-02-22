from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.tags import Tag
from app.db.session import create_sessionmaker


def test_models_tags_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm_tags.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            t = Tag(name="tag1", translated_name="标签1")
            session.add(t)
            await session.commit()
            await session.refresh(t)
            assert t.id == 1

        async with Session() as session:
            fetched = (await session.execute(select(Tag))).scalars().first()
            assert fetched is not None
            assert fetched.name == "tag1"

        await engine.dispose()

    asyncio.run(_run())

