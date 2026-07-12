from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.coerce import clamp_int
from app.core.time import iso_utc_ms
from app.db.session import create_sessionmaker, with_sqlite_busy_retry

DEFAULT_JOBS_KEEP_DAYS = 14
DEFAULT_JOBS_MAX_DELETE_ROWS = 50_000
DEFAULT_JOBS_CHUNK_SIZE = 1_000

# Never purge active work. Terminal statuses only.
TERMINAL_JOB_STATUSES = ("completed", "failed", "canceled", "dlq")


@dataclass(frozen=True, slots=True)
class JobsCleanupResult:
    cutoff: str
    deleted: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class JobsCleanupPreview:
    cutoff: str
    would_delete: int
    has_more: bool


def _cutoff_iso(*, keep_days: int, now: datetime | None = None) -> str:
    keep_days_i = int(keep_days)
    if keep_days_i < 0:
        raise ValueError("keep_days must be >= 0")

    now_dt = now or datetime.now(timezone.utc)
    cutoff_dt = now_dt - timedelta(days=keep_days_i)
    return iso_utc_ms(cutoff_dt)


async def preview_jobs_cleanup(
    engine: AsyncEngine,
    *,
    keep_days: int = DEFAULT_JOBS_KEEP_DAYS,
    max_delete_rows: int = DEFAULT_JOBS_MAX_DELETE_ROWS,
    now: datetime | None = None,
) -> JobsCleanupPreview:
    cutoff = _cutoff_iso(keep_days=int(keep_days), now=now)
    max_delete_rows_i = clamp_int(int(max_delete_rows), min_v=1, max_v=10_000_000)
    status_list = list(TERMINAL_JOB_STATUSES)

    Session = create_sessionmaker(engine)
    sql = """
SELECT id
FROM jobs
WHERE status IN :statuses
  AND updated_at < :cutoff
ORDER BY updated_at ASC
LIMIT :limit;
""".strip()

    async def _op() -> JobsCleanupPreview:
        async with Session() as session:
            res = await session.execute(
                sa.text(sql).bindparams(sa.bindparam("statuses", expanding=True)),
                {"cutoff": cutoff, "limit": int(max_delete_rows_i) + 1, "statuses": status_list},
            )
            rows = res.all()
            would_delete = min(len(rows), int(max_delete_rows_i))
            has_more = len(rows) > int(max_delete_rows_i)
            return JobsCleanupPreview(cutoff=cutoff, would_delete=would_delete, has_more=has_more)

    return await with_sqlite_busy_retry(_op)


async def cleanup_jobs(
    engine: AsyncEngine,
    *,
    keep_days: int = DEFAULT_JOBS_KEEP_DAYS,
    max_delete_rows: int = DEFAULT_JOBS_MAX_DELETE_ROWS,
    chunk_size: int = DEFAULT_JOBS_CHUNK_SIZE,
    now: datetime | None = None,
) -> JobsCleanupResult:
    cutoff = _cutoff_iso(keep_days=int(keep_days), now=now)
    max_delete_rows_i = clamp_int(int(max_delete_rows), min_v=1, max_v=10_000_000)
    chunk_size_i = clamp_int(int(chunk_size), min_v=1, max_v=100_000)
    chunk_size_i = min(int(chunk_size_i), int(max_delete_rows_i))
    status_list = list(TERMINAL_JOB_STATUSES)

    Session = create_sessionmaker(engine)

    delete_sql = """
DELETE FROM jobs
WHERE id IN (
  SELECT id
  FROM jobs
  WHERE status IN :statuses
    AND updated_at < :cutoff
  ORDER BY updated_at ASC
  LIMIT :chunk
);
""".strip()

    has_more_sql = """
SELECT 1
FROM jobs
WHERE status IN :statuses
  AND updated_at < :cutoff
LIMIT 1;
""".strip()

    async def _op() -> JobsCleanupResult:
        deleted = 0
        async with Session() as session:
            while deleted < int(max_delete_rows_i):
                remaining = int(max_delete_rows_i) - deleted
                chunk = min(int(chunk_size_i), remaining)
                res = await session.execute(
                    sa.text(delete_sql).bindparams(sa.bindparam("statuses", expanding=True)),
                    {"cutoff": cutoff, "chunk": int(chunk), "statuses": status_list},
                )
                n = int(res.rowcount or 0)
                if n <= 0:
                    break
                deleted += n
                await session.commit()
                if n < int(chunk):
                    break

            more = await session.execute(
                sa.text(has_more_sql).bindparams(sa.bindparam("statuses", expanding=True)),
                {"cutoff": cutoff, "statuses": status_list},
            )
            has_more = more.first() is not None
            return JobsCleanupResult(cutoff=cutoff, deleted=deleted, has_more=has_more)

    return await with_sqlite_busy_retry(_op)
