from __future__ import annotations

import asyncio
from pathlib import Path

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

            idx_info = (await conn.exec_driver_sql("PRAGMA index_info('idx_images_filter')")).mappings().all()
            cols = [str(r.get("name")) for r in idx_info]
            assert "random_key" in cols

        await engine.dispose()

    asyncio.run(_run())

