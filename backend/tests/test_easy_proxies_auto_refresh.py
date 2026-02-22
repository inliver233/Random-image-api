from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from app.db.engine import create_engine
from app.db.models.base import Base
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker
from app.easy_proxies.auto_refresh import EasyProxiesAutoRefreshConfig, EasyProxiesAutoRefresher


def test_easy_proxies_auto_refresher_enqueues_at_interval_and_skips_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "auto_refresh.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    now_box = {"now": 0.0}

    def now() -> float:
        return float(now_box["now"])

    engine = create_engine(db_url)

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        refresher = EasyProxiesAutoRefresher(
            EasyProxiesAutoRefreshConfig(base_url="http://easy-proxies:9090", interval_s=10.0),
            now=now,
        )

        await refresher.tick(engine)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            jobs = (await session.execute(select(JobRow))).scalars().all()
            assert len(jobs) == 1
            payload = json.loads(jobs[0].payload_json)
            assert payload["base_url"] == "http://easy-proxies:9090"
            assert payload["conflict_policy"] == "skip_non_easy_proxies"

        now_box["now"] = 11.0
        await refresher.tick(engine)
        async with Session() as session:
            jobs = (await session.execute(select(JobRow))).scalars().all()
            assert len(jobs) == 1

            jobs[0].status = "completed"
            await session.commit()

        now_box["now"] = 21.0
        await refresher.tick(engine)
        async with Session() as session:
            jobs = (await session.execute(select(JobRow).order_by(JobRow.id.asc()))).scalars().all()
            assert len(jobs) == 2

        await engine.dispose()

    asyncio.run(_run())

