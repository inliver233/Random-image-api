from __future__ import annotations

import asyncio
from pathlib import Path

from app.db.engine import create_engine
from app.jobs.queue import JobQueuePort, SqliteJobQueue, build_job_queue, resolve_job_queue


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


def test_build_job_queue_defaults_to_sqlite(tmp_path: Path) -> None:
    engine = create_engine(_sqlite_url(tmp_path / "q.db"))
    q = build_job_queue(engine)
    assert isinstance(q, SqliteJobQueue)
    assert q.backend == "sqlite"
    assert isinstance(q, JobQueuePort)

    # redis/nats are reserved — fail loud (no silent sqlite fallback).
    try:
        build_job_queue(engine, backend="redis")
        raise AssertionError("expected ValueError for redis")
    except ValueError as exc:
        assert "not implemented" in str(exc).lower() or "reserved" in str(exc).lower()
    try:
        build_job_queue(engine, backend="nats")
        raise AssertionError("expected ValueError for nats")
    except ValueError as exc:
        assert "not implemented" in str(exc).lower() or "reserved" in str(exc).lower()

    # memory is an implemented alias: same SqliteJobQueue, honest backend label.
    q_mem = build_job_queue(engine, backend="memory")
    assert isinstance(q_mem, SqliteJobQueue)
    assert q_mem.backend == "memory"

    # resolve prefers injected port.
    assert resolve_job_queue(q, engine) is q
    assert resolve_job_queue(None, engine).backend == "sqlite"


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


def test_sqlite_job_queue_enqueue_and_opportunistic(tmp_path: Path) -> None:
    engine = create_engine(_sqlite_url(tmp_path / "q3.db"))

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

        q = build_job_queue(engine)
        jid = await q.enqueue(type="noop", payload_json="{}", priority=1)
        assert int(jid) >= 1

        a = await q.enqueue_opportunistic_hydrate(illust_id=99, reason="test")
        b = await q.enqueue_opportunistic_hydrate(illust_id=99, reason="again")
        assert a is not None
        assert b is None

        await engine.dispose()

    asyncio.run(_run())
