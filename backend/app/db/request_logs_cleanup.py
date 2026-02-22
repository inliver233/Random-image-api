from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.time import iso_utc_ms
from app.db.session import create_sessionmaker, with_sqlite_busy_retry

DEFAULT_REQUEST_LOGS_KEEP_DAYS = 30
DEFAULT_REQUEST_LOGS_MAX_DELETE_ROWS = 50_000
DEFAULT_REQUEST_LOGS_CHUNK_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class RequestLogsCleanupResult:
    cutoff: str
    deleted: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class RequestLogsCleanupPreview:
    cutoff: str
    would_delete: int
    has_more: bool


def _clamp_int(value: int, *, min_v: int, max_v: int) -> int:
    return max(min_v, min(int(value), max_v))


def _cutoff_iso(*, keep_days: int, now: datetime | None = None) -> str:
    keep_days_i = int(keep_days)
    if keep_days_i < 0:
        raise ValueError("keep_days must be >= 0")

    now_dt = now or datetime.now(timezone.utc)
    cutoff_dt = now_dt - timedelta(days=keep_days_i)
    return iso_utc_ms(cutoff_dt)


async def preview_request_logs_cleanup(
    engine: AsyncEngine,
    *,
    keep_days: int = DEFAULT_REQUEST_LOGS_KEEP_DAYS,
    max_delete_rows: int = DEFAULT_REQUEST_LOGS_MAX_DELETE_ROWS,
    now: datetime | None = None,
) -> RequestLogsCleanupPreview:
    cutoff = _cutoff_iso(keep_days=int(keep_days), now=now)
    max_delete_rows_i = _clamp_int(int(max_delete_rows), min_v=1, max_v=10_000_000)

    Session = create_sessionmaker(engine)
    sql = """
SELECT id
FROM request_logs
WHERE created_at < :cutoff
ORDER BY created_at ASC
LIMIT :limit;
""".strip()

    async def _op() -> RequestLogsCleanupPreview:
        async with Session() as session:
            res = await session.execute(sa.text(sql), {"cutoff": cutoff, "limit": int(max_delete_rows_i) + 1})
            rows = res.all()
            would_delete = min(len(rows), int(max_delete_rows_i))
            has_more = len(rows) > int(max_delete_rows_i)
            return RequestLogsCleanupPreview(cutoff=cutoff, would_delete=would_delete, has_more=has_more)

    return await with_sqlite_busy_retry(_op)


async def cleanup_request_logs(
    engine: AsyncEngine,
    *,
    keep_days: int = DEFAULT_REQUEST_LOGS_KEEP_DAYS,
    max_delete_rows: int = DEFAULT_REQUEST_LOGS_MAX_DELETE_ROWS,
    chunk_size: int = DEFAULT_REQUEST_LOGS_CHUNK_SIZE,
    now: datetime | None = None,
) -> RequestLogsCleanupResult:
    cutoff = _cutoff_iso(keep_days=int(keep_days), now=now)
    max_delete_rows_i = _clamp_int(int(max_delete_rows), min_v=1, max_v=10_000_000)
    chunk_size_i = _clamp_int(int(chunk_size), min_v=1, max_v=100_000)
    chunk_size_i = min(int(chunk_size_i), int(max_delete_rows_i))

    Session = create_sessionmaker(engine)

    delete_sql = """
DELETE FROM request_logs
WHERE id IN (
  SELECT id
  FROM request_logs
  WHERE created_at < :cutoff
  ORDER BY created_at ASC
  LIMIT :chunk
);
""".strip()

    has_more_sql = """
SELECT 1
FROM request_logs
WHERE created_at < :cutoff
LIMIT 1;
""".strip()

    async def _op() -> RequestLogsCleanupResult:
        deleted = 0
        has_more = False

        async with Session() as session:
            while deleted < int(max_delete_rows_i):
                chunk = min(int(chunk_size_i), int(max_delete_rows_i) - int(deleted))
                result = await session.execute(sa.text(delete_sql), {"cutoff": cutoff, "chunk": int(chunk)})
                n = int(result.rowcount or 0)
                if n <= 0:
                    await session.commit()
                    break
                deleted += n
                await session.commit()

            if deleted >= int(max_delete_rows_i):
                res = await session.execute(sa.text(has_more_sql), {"cutoff": cutoff})
                has_more = res.first() is not None

        return RequestLogsCleanupResult(cutoff=cutoff, deleted=int(deleted), has_more=bool(has_more))

    return await with_sqlite_busy_retry(_op)

