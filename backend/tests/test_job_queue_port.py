from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.engine import create_engine
from app.jobs.queue import JobQueuePort, SqliteJobQueue, build_job_queue


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


def test_build_job_queue_defaults_to_sqlite(tmp_path: Path) -> None:
    engine = create_engine(_sqlite_url(tmp_path / "q.db"))
    q = build_job_queue(engine)
    assert isinstance(q, SqliteJobQueue)
    assert q.backend == "sqlite"
    assert isinstance(q, JobQueuePort)

    # Unknown / future backends fall back to sqlite (no hard fail).
    q2 = build_job_queue(engine, backend="redis")
    assert q2.backend == "sqlite"


def test_sqlite_job_queue_claim_roundtrip(tmp_path: Path) -> None:
    engine = create_engine(_sqlite_url(tmp_path / "q2.db"))

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

        q = build_job_queue(engine)
        row = await q.claim_next(worker_id="w1")
        assert row is not None
        assert row["status"] == "running"
        assert row["locked_by"] == "w1"
        assert await q.renew_lock(job_id=int(row["id"]), worker_id="w1") is True
        assert await q.claim_next(worker_id="w2") is None
        await engine.dispose()

    asyncio.run(_run())
