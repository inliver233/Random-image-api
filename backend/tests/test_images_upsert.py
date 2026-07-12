from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.engine import create_engine
from app.db.images_upsert import (
    adapt_driver_sql_named_binds,
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


def test_utc_now_compiles_per_dialect() -> None:
    from sqlalchemy.dialects import postgresql, sqlite

    from app.db.utc_text_now import UtcNow

    sqlite_sql = str(UtcNow().compile(dialect=sqlite.dialect()))
    pg_sql = str(UtcNow().compile(dialect=postgresql.dialect()))
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


def test_adapt_driver_sql_named_binds_sqlite_passthrough() -> None:
    sql = "SELECT 1 WHERE key = :key AND t <= :now AND cast_col::text IS NOT NULL"
    params = {"key": "k", "now": "t0"}
    out_sql, out_params = adapt_driver_sql_named_binds(sql, params, dialect_name="sqlite")
    assert out_sql == sql
    assert out_params is params


def test_adapt_driver_sql_named_binds_postgres_numeric_dollar() -> None:
    sql = """
UPDATE jobs
SET locked_by=:worker_id, locked_at=:now, updated_at=:now
WHERE id=:id AND status='pending' AND note::text IS NOT NULL
RETURNING *;
""".strip()
    params = {"worker_id": "w1", "now": "t0", "id": 7}
    out_sql, out_params = adapt_driver_sql_named_binds(sql, params, dialect_name="postgresql")
    assert ":worker_id" not in out_sql
    assert ":now" not in out_sql
    assert ":id" not in out_sql
    assert "note::text" in out_sql  # postgres cast preserved
    assert "$1" in out_sql and "$2" in out_sql and "$3" in out_sql
    # repeated :now reuses the same dollar index
    assert out_sql.count("$2") >= 2
    assert out_params == ("w1", "t0", 7)


def test_adapt_driver_sql_named_binds_empty_params() -> None:
    sql = "SELECT 1"
    out_sql, out_params = adapt_driver_sql_named_binds(sql, None, dialect_name="postgresql")
    assert out_sql == sql
    assert out_params is None
