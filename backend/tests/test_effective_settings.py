from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.core.effective_settings import load_effective_settings
from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.runtime_settings import RuntimeSetting
from app.db.session import create_sessionmaker


def test_effective_settings_merge_and_reload(tmp_path: Path) -> None:
    db_path = tmp_path / "effective_settings.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()
    engine = create_engine(db_url)

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)

        base = await load_effective_settings(engine, env={"APP_ENV": "dev", "DATABASE_URL": db_url})
        assert base.runtime.proxy_enabled is False

        async with Session() as session:
            session.add(RuntimeSetting(key="proxy.enabled", value_json="true", updated_by="tester"))
            await session.commit()

        updated = await load_effective_settings(engine, env={"APP_ENV": "dev", "DATABASE_URL": db_url})
        assert updated.runtime.proxy_enabled is True

        async with Session() as session:
            row = (await session.execute(select(RuntimeSetting).where(RuntimeSetting.key == "proxy.enabled"))).scalar_one()
            row.value_json = "false"
            await session.commit()

        updated2 = await load_effective_settings(engine, env={"APP_ENV": "dev", "DATABASE_URL": db_url})
        assert updated2.runtime.proxy_enabled is False

        await engine.dispose()

    asyncio.run(_run())

