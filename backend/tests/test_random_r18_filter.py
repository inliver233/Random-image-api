from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.images import Image
from app.db.random_pick import pick_random_image
from app.db.session import create_sessionmaker


def test_r18_strict_excludes_null_x_restrict(tmp_path: Path) -> None:
    db_path = tmp_path / "r18_strict.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            session.add(
                Image(
                    illust_id=1,
                    page_index=0,
                    ext="jpg",
                    original_url="https://example.test/1.jpg",
                    proxy_path="/i/1.jpg",
                    random_key=0.5,
                    x_restrict=None,
                )
            )
            await session.commit()

        async with Session() as session:
            strict = await pick_random_image(session, r=0.0, r18=0, r18_strict=True)
            non_strict = await pick_random_image(session, r=0.0, r18=0, r18_strict=False)
            assert strict is None
            assert non_strict is not None
            assert int(non_strict.illust_id) == 1

        await engine.dispose()

    asyncio.run(_run())


def test_r18_filter_selects_only_r18(tmp_path: Path) -> None:
    db_path = tmp_path / "r18_only.db"
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
                        original_url="https://example.test/safe.jpg",
                        proxy_path="/i/1.jpg",
                        random_key=0.1,
                        x_restrict=0,
                    ),
                    Image(
                        illust_id=11,
                        page_index=0,
                        ext="jpg",
                        original_url="https://example.test/r18.jpg",
                        proxy_path="/i/2.jpg",
                        random_key=0.2,
                        x_restrict=1,
                    ),
                ]
            )
            await session.commit()

        async with Session() as session:
            picked = await pick_random_image(session, r=0.0, r18=1, r18_strict=True)
            assert picked is not None
            assert int(picked.illust_id) == 11

        await engine.dispose()

    asyncio.run(_run())

