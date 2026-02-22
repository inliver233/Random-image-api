from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.imports import Import
from app.db.models.tags import Tag
from app.db.session import create_sessionmaker


def test_models_image_tags_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm_image_tags.db"
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

            tag = Tag(name="tag1")
            session.add(tag)
            await session.flush()

            session.add(ImageTag(image_id=img.id, tag_id=tag.id))
            await session.commit()

        async with Session() as session:
            link = (await session.execute(select(ImageTag))).scalars().first()
            assert link is not None
            assert link.image_id == 1
            assert link.tag_id == 1

        await engine.dispose()

    asyncio.run(_run())

