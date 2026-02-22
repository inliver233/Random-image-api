from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.runtime_settings import RuntimeSetting
from app.db.session import create_sessionmaker


def test_models_runtime_settings_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm_runtime_settings.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            row = RuntimeSetting(key="k1", value_json="{}", description="d", updated_by="tester")
            session.add(row)
            await session.commit()

        async with Session() as session:
            fetched = (await session.execute(select(RuntimeSetting))).scalars().first()
            assert fetched is not None
            assert fetched.key == "k1"
            assert fetched.value_json == "{}"

        await engine.dispose()

    asyncio.run(_run())

