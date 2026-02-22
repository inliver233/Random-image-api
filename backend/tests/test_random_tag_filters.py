from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.tags import Tag
from app.db.random_pick import pick_random_image
from app.db.session import create_sessionmaker


def test_included_tags_filters_images(tmp_path: Path) -> None:
    db_path = tmp_path / "included_tags.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            cat = Tag(name="cat")
            dog = Tag(name="dog")
            session.add_all([cat, dog])

            img_cat = Image(
                illust_id=1,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/cat.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
                x_restrict=0,
            )
            img_dog = Image(
                illust_id=2,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/dog.jpg",
                proxy_path="/i/2.jpg",
                random_key=0.2,
                x_restrict=0,
            )
            session.add_all([img_cat, img_dog])
            await session.flush()

            session.add_all(
                [
                    ImageTag(image_id=img_cat.id, tag_id=cat.id),
                    ImageTag(image_id=img_dog.id, tag_id=dog.id),
                ]
            )
            await session.commit()

        async with Session() as session:
            picked = await pick_random_image(session, r=0.0, included_tags=["cat"])
            assert picked is not None
            assert int(picked.illust_id) == 1

        await engine.dispose()

    asyncio.run(_run())


def test_included_tags_requires_all(tmp_path: Path) -> None:
    db_path = tmp_path / "included_all.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            cat = Tag(name="cat")
            dog = Tag(name="dog")
            session.add_all([cat, dog])

            img_cat_only = Image(
                illust_id=10,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/cat_only.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
                x_restrict=0,
            )
            img_both = Image(
                illust_id=11,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/both.jpg",
                proxy_path="/i/2.jpg",
                random_key=0.2,
                x_restrict=0,
            )
            session.add_all([img_cat_only, img_both])
            await session.flush()

            session.add_all(
                [
                    ImageTag(image_id=img_cat_only.id, tag_id=cat.id),
                    ImageTag(image_id=img_both.id, tag_id=cat.id),
                    ImageTag(image_id=img_both.id, tag_id=dog.id),
                ]
            )
            await session.commit()

        async with Session() as session:
            picked = await pick_random_image(session, r=0.0, included_tags=["cat", "dog"])
            assert picked is not None
            assert int(picked.illust_id) == 11

        await engine.dispose()

    asyncio.run(_run())


def test_excluded_tags_filters_images(tmp_path: Path) -> None:
    db_path = tmp_path / "excluded_tags.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            cat = Tag(name="cat")
            dog = Tag(name="dog")
            session.add_all([cat, dog])

            img_ok = Image(
                illust_id=20,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/ok.jpg",
                proxy_path="/i/1.jpg",
                random_key=0.1,
                x_restrict=0,
            )
            img_bad = Image(
                illust_id=21,
                page_index=0,
                ext="jpg",
                original_url="https://example.test/bad.jpg",
                proxy_path="/i/2.jpg",
                random_key=0.2,
                x_restrict=0,
            )
            session.add_all([img_ok, img_bad])
            await session.flush()

            session.add_all(
                [
                    ImageTag(image_id=img_ok.id, tag_id=cat.id),
                    ImageTag(image_id=img_bad.id, tag_id=dog.id),
                ]
            )
            await session.commit()

        async with Session() as session:
            picked = await pick_random_image(session, r=0.0, excluded_tags=["dog"])
            assert picked is not None
            assert int(picked.illust_id) == 20

        await engine.dispose()

    asyncio.run(_run())

