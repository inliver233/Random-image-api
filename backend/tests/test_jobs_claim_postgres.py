"""PostgreSQL job-claim safety (B3).

The claim CTE was a plain ``SELECT ... LIMIT 1``: two PostgreSQL
transactions could select the same candidate id, and the second UPDATE
would overwrite ``locked_by`` after the first committed — the same job
executed twice. The PG claim now locks the candidate row with
``FOR UPDATE SKIP LOCKED`` inside the claiming transaction.

The SQL-shape tests always run. The two-real-connections concurrency
test needs a live PostgreSQL and is gated on ``TEST_POSTGRES_URL``
(e.g. ``postgresql+asyncpg://ria:ria@127.0.0.1:5432/ria_test``); it is
an environment-dependent integration test, not a masked failure — CI
with a PG service must set the variable.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.jobs.claim import claim_next_job, claim_next_job_sql

LIVE_PG_URL = (os.environ.get("TEST_POSTGRES_URL") or "").strip()


def test_claim_sql_postgres_locks_candidate_row() -> None:
    sql = claim_next_job_sql("postgresql")
    # The lock must be part of the candidate selection, not the outer UPDATE.
    assert sql.index("FOR UPDATE SKIP LOCKED") < sql.index("UPDATE jobs")


def test_claim_sql_sqlite_stays_plain() -> None:
    sql = claim_next_job_sql("sqlite")
    assert "FOR UPDATE" not in sql
    assert "SKIP LOCKED" not in sql


@pytest.mark.skipif(not LIVE_PG_URL, reason="TEST_POSTGRES_URL not set (live PostgreSQL integration)")
def test_postgres_concurrent_claims_never_double_claim() -> None:
    from app.db.engine import create_engine

    engine = create_engine(LIVE_PG_URL)

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("DROP TABLE IF EXISTS jobs")
            await conn.exec_driver_sql(
                """
CREATE TABLE jobs (
  id BIGSERIAL PRIMARY KEY,
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
)
""".strip()
            )
            for _ in range(3):
                await conn.exec_driver_sql(
                    "INSERT INTO jobs (type,status,priority,payload_json) VALUES ('noop','pending',0,'{}')"
                )

        start = asyncio.Event()

        async def _claim(worker_id: str):
            await start.wait()
            return await claim_next_job(engine, worker_id=worker_id)

        # More claimers than jobs, all released at once: every claimed id
        # must be unique, and no claimer may hang.
        tasks = [asyncio.create_task(_claim(f"w{i}")) for i in range(5)]
        start.set()
        rows = await asyncio.gather(*tasks)

        claimed = [r for r in rows if r is not None]
        claimed_ids = [int(r["id"]) for r in claimed]
        assert len(claimed_ids) == len(set(claimed_ids)), f"double claim: {claimed_ids}"
        assert len(claimed_ids) == 3
        for row in claimed:
            assert row["status"] == "running"

        async with engine.connect() as conn:
            result = await conn.exec_driver_sql("SELECT id, locked_by FROM jobs ORDER BY id")
            by_id = {int(r[0]): r[1] for r in result}
        for row in claimed:
            assert by_id[int(row["id"])] == row["locked_by"]

        async with engine.begin() as conn:
            await conn.exec_driver_sql("DROP TABLE IF EXISTS jobs")
        await engine.dispose()

    asyncio.run(_run())
