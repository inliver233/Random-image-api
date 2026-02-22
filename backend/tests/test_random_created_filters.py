from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.random_pick import pick_random_image
from app.db.session import create_sessionmaker


def test_created_from_to_filters(tmp_path: Path) -> None:
    db_path = tmp_path / "created_filters.db"
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
                        original_url="https://example.test/old.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.1,
                        x_restrict=0,
                        created_at_pixiv="2024-01-01T00:00:00Z",
                    ),
                    Image(
                        illust_id=2,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/new.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.2,
                        x_restrict=0,
                        created_at_pixiv="2024-06-01T00:00:00Z",
                    ),
                ]
            )
            await session.commit()

        async with Session() as session:
            picked_from = await pick_random_image(session, r=0.0, created_from="2024-05-01T00:00:00Z")
            assert picked_from is not None
            assert int(picked_from.illust_id) == 2

        async with Session() as session:
            picked_to = await pick_random_image(session, r=0.0, created_to="2024-02-01T00:00:00Z")
            assert picked_to is not None
            assert int(picked_to.illust_id) == 1

        async with Session() as session:
            picked_between = await pick_random_image(
                session,
                r=0.0,
                created_from="2024-01-01T00:00:00Z",
                created_to="2024-12-31T23:59:59Z",
            )
            assert picked_between is not None

        await engine.dispose()

    asyncio.run(_run())

