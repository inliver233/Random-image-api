from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.random_pick import pick_random_image
from app.db.session import create_sessionmaker


def test_user_id_and_illust_id_filters(tmp_path: Path) -> None:
    db_path = tmp_path / "user_illust_filters.db"
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
                        random_key=0.1,
                        x_restrict=0,
                        user_id=100,
                    ),
                    Image(
                        illust_id=2,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/2.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.2,
                        x_restrict=0,
                        user_id=101,
                    ),
                ]
            )
            await session.commit()

        async with Session() as session:
            picked_user = await pick_random_image(session, r=0.0, user_id=100)
            assert picked_user is not None
            assert int(picked_user.illust_id) == 1

        async with Session() as session:
            picked_illust = await pick_random_image(session, r=0.0, illust_id=2)
            assert picked_illust is not None
            assert int(picked_illust.user_id or 0) == 101

        await engine.dispose()

    asyncio.run(_run())

