from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import create_engine
from app.db.images_upsert import (
    dialect_name_from_engine,
    driver_param_marker,
    insert_for_dialect,
    now_expr_for_dialect,
    upsert_image_by_illust_page,
)
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


def test_insert_for_dialect_selects_sqlite_and_postgres_builders() -> None:
    sqlite_builder = insert_for_dialect(Image, dialect_name="sqlite")
    pg_builder = insert_for_dialect(Image, dialect_name="postgresql")
    # Identity: same insert constructor family as dialect-specific modules.
    assert type(sqlite_builder) is type(sqlite_insert(Image))
    assert type(pg_builder) is type(pg_insert(Image))
    # Default / unknown → sqlite path (safe for current default DB).
    other = insert_for_dialect(Image, dialect_name="other")
    assert type(other) is type(sqlite_insert(Image))


def test_now_expr_for_dialect_sqlite_uses_strftime_postgres_uses_to_char() -> None:
    sqlite_sql = str(now_expr_for_dialect("sqlite"))
    pg_sql = str(now_expr_for_dialect("postgresql"))
    assert "strftime" in sqlite_sql
    assert "to_char" in pg_sql
    assert "UTC" in pg_sql


def test_driver_param_marker_sqlite_question_postgres_percent_s() -> None:
    assert driver_param_marker("sqlite") == "?"
    assert driver_param_marker("postgresql") == "%s"
    assert driver_param_marker("other") == "?"


def test_dialect_name_from_engine_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "dialect_engine.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())
    try:
        assert dialect_name_from_engine(engine) == "sqlite"
    finally:
        asyncio.run(engine.dispose())
