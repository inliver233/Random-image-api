from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.metrics import JOBS_CLAIM_TOTAL
from app.db.session import with_sqlite_busy_retry

DEFAULT_LOCK_TTL_S = 300


def _iso_utc_ms(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"


async def claim_next_job(
    engine: AsyncEngine,
    *,
    worker_id: str,
    lock_ttl_s: int = DEFAULT_LOCK_TTL_S,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    now_dt = now or datetime.now(timezone.utc)
    expired_before_dt = now_dt - timedelta(seconds=lock_ttl_s)
    now_s = _iso_utc_ms(now_dt)
    expired_before_s = _iso_utc_ms(expired_before_dt)

    sql = """
WITH candidate AS (
  SELECT id
  FROM jobs
  WHERE status IN ('pending','failed','running')
    AND (run_after IS NULL OR run_after <= :now)
    AND (locked_at IS NULL OR locked_at <= :lock_expired_before)
  ORDER BY priority DESC, id ASC
  LIMIT 1
)
UPDATE jobs
SET status='running',
    locked_by=:worker_id,
    locked_at=:now,
    updated_at=:now
WHERE id IN (SELECT id FROM candidate)
RETURNING *;
""".strip()

    async def _op() -> dict[str, Any] | None:
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql(
                sql,
                {
                    "now": now_s,
                    "lock_expired_before": expired_before_s,
                    "worker_id": worker_id,
                },
            )
            row = result.mappings().first()
            return dict(row) if row else None

    job = await with_sqlite_busy_retry(_op)
    if job is not None:
        JOBS_CLAIM_TOTAL.inc()
    return job


async def renew_job_lock(
    engine: AsyncEngine,
    *,
    job_id: int,
    worker_id: str,
    now: datetime | None = None,
) -> bool:
    now_dt = now or datetime.now(timezone.utc)
    now_s = _iso_utc_ms(now_dt)

    sql = """
UPDATE jobs
SET locked_at=:now,
    updated_at=:now
WHERE id=:id AND locked_by=:worker_id AND status='running';
""".strip()

    async def _op() -> bool:
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql(
                sql,
                {"now": now_s, "id": job_id, "worker_id": worker_id},
            )
            return (result.rowcount or 0) == 1

    return await with_sqlite_busy_retry(_op)
