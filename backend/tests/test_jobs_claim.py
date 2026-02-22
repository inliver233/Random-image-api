from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db.engine import create_engine
from app.jobs.claim import claim_next_job, renew_job_lock


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


def test_jobs_claim_multi_worker_no_double_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
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
  locked_at TEXT
);
""".strip()
            )
            await conn.exec_driver_sql(
                """
INSERT INTO jobs (type,status,priority,run_after,payload_json,created_at,updated_at)
VALUES ('noop','pending',0,NULL,'{}','2026-02-10T00:00:00.000Z','2026-02-10T00:00:00.000Z');
""".strip()
            )

        start = asyncio.Event()

        async def _claim(worker_id: str):
            await start.wait()
            return await claim_next_job(engine, worker_id=worker_id)

        t1 = asyncio.create_task(_claim("w1"))
        t2 = asyncio.create_task(_claim("w2"))
        start.set()
        r1, r2 = await asyncio.gather(t1, t2)

        assert (r1 is None) != (r2 is None)
        winner = r1 or r2
        assert winner is not None
        assert winner["status"] == "running"
        assert winner["locked_by"] in {"w1", "w2"}

        async with engine.connect() as conn:
            row = (await conn.exec_driver_sql("SELECT status, locked_by FROM jobs WHERE id=1")).one()
            assert row[0] == "running"
            assert row[1] == winner["locked_by"]

        await engine.dispose()

    asyncio.run(_run())


def test_jobs_claim_ttl_reclaim_running_job(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs2.db"
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
  locked_at TEXT
);
""".strip()
            )

            await conn.exec_driver_sql(
                """
INSERT INTO jobs (type,status,priority,run_after,payload_json,locked_by,locked_at,created_at,updated_at)
VALUES ('noop','running',0,NULL,'{}','w1','2026-02-10T00:00:00.000Z','2026-02-10T00:00:00.000Z','2026-02-10T00:00:00.000Z');
""".strip()
            )

        now = datetime(2026, 2, 10, 0, 10, 0, tzinfo=timezone.utc)
        job = await claim_next_job(engine, worker_id="w2", lock_ttl_s=60, now=now)
        assert job is not None
        assert job["id"] == 1
        assert job["locked_by"] == "w2"
        assert job["status"] == "running"

        await engine.dispose()

    asyncio.run(_run())


def test_jobs_claim_heartbeat_renew(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs3.db"
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
  locked_at TEXT
);
""".strip()
            )

            await conn.exec_driver_sql(
                """
INSERT INTO jobs (type,status,priority,run_after,payload_json,locked_by,locked_at,created_at,updated_at)
VALUES ('noop','running',0,NULL,'{}','w1','2026-02-10T00:00:00.000Z','2026-02-10T00:00:00.000Z','2026-02-10T00:00:00.000Z');
""".strip()
            )

        now = datetime(2026, 2, 10, 0, 0, 30, tzinfo=timezone.utc)
        assert await renew_job_lock(engine, job_id=1, worker_id="w1", now=now) is True
        assert await renew_job_lock(engine, job_id=1, worker_id="w2", now=now + timedelta(seconds=1)) is False

        await engine.dispose()

    asyncio.run(_run())

