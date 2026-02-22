from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.images_upsert import upsert_image_by_illust_page
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.session import create_sessionmaker


def test_images_upsert_by_illust_page(tmp_path: Path) -> None:
    db_path = tmp_path / "images_upsert.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)

        async with Session() as session:
            image_id1 = await upsert_image_by_illust_page(
                session,
                illust_id=123,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/old.jpg",
                proxy_path="/i/123_p0.jpg",
                random_key=0.1,
                created_import_id=None,
            )
            await session.commit()

        async with Session() as session:
            image_id2 = await upsert_image_by_illust_page(
                session,
                illust_id=123,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/new.jpg",
                proxy_path="/i/123_p0.jpg",
                random_key=0.2,
                created_import_id=None,
            )
            await session.commit()

        assert image_id1 == image_id2

        async with Session() as session:
            imgs = (await session.execute(select(Image))).scalars().all()
            assert len(imgs) == 1
            assert imgs[0].original_url == "https://example.test/new.jpg"
            assert imgs[0].random_key == 0.1

        await engine.dispose()

    asyncio.run(_run())
