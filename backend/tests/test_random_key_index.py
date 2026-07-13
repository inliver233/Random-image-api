from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.db.engine import create_engine
from app.db.models.base import Base


def test_images_has_random_key_index(tmp_path: Path) -> None:
    db_path = tmp_path / "random_key_index.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with engine.connect() as conn:
            idx_list = (await conn.exec_driver_sql("PRAGMA index_list('images')")).mappings().all()
            names = {str(r.get('name')) for r in idx_list}
            assert "idx_images_filter" in names
            assert "idx_images_status_x_random" in names

            idx_info = (await conn.exec_driver_sql("PRAGMA index_info('idx_images_filter')")).mappings().all()
            cols = [str(r.get("name")) for r in idx_info]
            assert "random_key" in cols

            hot_idx_info = (
                await conn.exec_driver_sql("PRAGMA index_info('idx_images_status_x_random')")
            ).mappings().all()
            hot_cols = [str(r.get("name")) for r in hot_idx_info]
            assert hot_cols == ["status", "x_restrict", "random_key"]

        await engine.dispose()

    asyncio.run(_run())


def test_default_random_pick_plan_uses_ordered_index(tmp_path: Path) -> None:
    db_path = tmp_path / "random_key_explain.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        where_sql = (
            "status = 1 AND x_restrict = 0 "
            "AND (last_fail_at IS NULL OR last_fail_at <= '2026-07-13T00:00:00Z')"
        )
        sqls = [
            "EXPLAIN QUERY PLAN SELECT id FROM images "
            f"WHERE {where_sql} AND random_key >= 0.5 ORDER BY random_key ASC LIMIT 1",
            "EXPLAIN QUERY PLAN SELECT id FROM images "
            f"WHERE {where_sql} ORDER BY random_key ASC LIMIT 1",
        ]
        async with engine.connect() as conn:
            plans = [(await conn.exec_driver_sql(sql)).all() for sql in sqls]
        for rows in plans:
            details = [str(row[3]) for row in rows]
            assert any("idx_images_status_x_random" in detail for detail in details), details
            assert not any("TEMP B-TREE FOR ORDER BY" in detail for detail in details), details
        await engine.dispose()

    asyncio.run(_run())


def test_default_random_pick_index_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "random_key_index_migration.db"
    database_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        cfg = Config(str(backend_dir / "alembic.ini"))
        command.upgrade(cfg, "head")
        with sqlite3.connect(db_path) as conn:
            names = {str(row[1]) for row in conn.execute("PRAGMA index_list('images')").fetchall()}
        assert "idx_images_status_x_random" in names

        command.downgrade(cfg, "20260713_0021")
        with sqlite3.connect(db_path) as conn:
            names = {str(row[1]) for row in conn.execute("PRAGMA index_list('images')").fetchall()}
        assert "idx_images_status_x_random" not in names
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous

