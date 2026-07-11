from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.engine import create_engine
from app.jobs.enqueue import enqueue_opportunistic_hydrate_metadata


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


def test_enqueue_opportunistic_hydrate_is_idempotent_under_race(tmp_path: Path) -> None:
    db_path = tmp_path / "enqueue_race.db"
    engine = create_engine(_sqlite_url(db_path))

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                """
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT,
  updated_at TEXT,
  type TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  run_after TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  payload_json TEXT NOT NULL,
  last_error TEXT,
  locked_by TEXT,
  locked_at TEXT,
  ref_type TEXT,
  ref_id TEXT
);
""".strip()
            )
            await conn.exec_driver_sql(
                """
CREATE UNIQUE INDEX uq_jobs_active_opportunistic_hydrate
ON jobs(type, ref_type, ref_id)
WHERE type = 'hydrate_metadata'
  AND ref_type = 'opportunistic_hydrate'
  AND status IN ('pending', 'running');
""".strip()
            )

        start = asyncio.Event()

        async def _one() -> int | None:
            await start.wait()
            return await enqueue_opportunistic_hydrate_metadata(engine, illust_id=4242, reason="test")

        tasks = [asyncio.create_task(_one()) for _ in range(8)]
        start.set()
        results = await asyncio.gather(*tasks)
        created = [r for r in results if r is not None]
        assert len(created) == 1

        async with engine.connect() as conn:
            count = (await conn.exec_driver_sql("SELECT COUNT(*) FROM jobs")).scalar_one()
            assert int(count) == 1

        # Second wave while still pending should create nothing.
        more = await asyncio.gather(
            *[enqueue_opportunistic_hydrate_metadata(engine, illust_id=4242, reason="again") for _ in range(4)]
        )
        assert all(x is None for x in more)

        await engine.dispose()

    asyncio.run(_run())
