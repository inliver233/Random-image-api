from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker


def test_models_jobs_insert_select(tmp_path: Path) -> None:
    db_path = tmp_path / "orm_jobs.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            row = JobRow(type="noop", status="pending", payload_json="{}")
            session.add(row)
            await session.commit()
            await session.refresh(row)
            assert row.id == 1

        async with Session() as session:
            fetched = (await session.execute(select(JobRow))).scalars().first()
            assert fetched is not None
            assert fetched.type == "noop"
            assert fetched.status == "pending"

        await engine.dispose()

    asyncio.run(_run())

