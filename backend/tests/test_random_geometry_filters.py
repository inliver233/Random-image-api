from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.random_pick import pick_random_image
from app.db.session import create_sessionmaker


def test_orientation_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "orientation_filter.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add_all(
                [
                    Image(
                        illust_id=1,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/1.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.2,
                        x_restrict=0,
                        orientation=1,
                    ),
                    Image(
                        illust_id=2,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/2.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.1,
                        x_restrict=0,
                        orientation=2,
                    ),
                ]
            )
            await session.commit()

        async with Session() as session:
            picked = await pick_random_image(session, r=0.0, orientation=1)
            assert picked is not None
            assert int(picked.illust_id) == 1

        await engine.dispose()

    asyncio.run(_run())


def test_min_width_height_pixels_filters(tmp_path: Path) -> None:
    db_path = tmp_path / "min_filters.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add_all(
                [
                    Image(
                        illust_id=10,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/small.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.1,
                        x_restrict=0,
                        width=800,
                        height=600,
                    ),
                    Image(
                        illust_id=11,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/large.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.2,
                        x_restrict=0,
                        width=1920,
                        height=1080,
                    ),
                ]
            )
            await session.commit()

        async with Session() as session:
            picked = await pick_random_image(session, r=0.0, min_width=1000, min_height=700)
            assert picked is not None
            assert int(picked.illust_id) == 11

        async with Session() as session:
            picked_pixels = await pick_random_image(session, r=0.0, min_pixels=1920 * 1080)
            assert picked_pixels is not None
            assert int(picked_pixels.illust_id) == 11

        await engine.dispose()

    asyncio.run(_run())

