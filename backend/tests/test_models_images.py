from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.models.imports import Import
from app.db.session import create_sessionmaker


def test_models_images_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm_images.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)

        async with Session() as session:
            imp = Import(created_by="tester", source="manual")
            session.add(imp)
            await session.flush()

            img = Image(
                illust_id=1,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/original.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.5,
                created_import_id=imp.id,
            )
            session.add(img)
            await session.commit()
            await session.refresh(img)
            assert img.id == 1

        async with Session() as session:
            fetched = (await session.execute(select(Image))).scalars().first()
            assert fetched is not None
            assert fetched.illust_id == 1
            assert fetched.page_index == 0

        await engine.dispose()

    asyncio.run(_run())

