from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.time import iso_utc_ms
from app.core.metrics import JOBS_FAILED_TOTAL
from app.db.session import is_sqlite_busy_error, with_sqlite_busy_retry
from app.jobs.dispatch import JobDispatcher
from app.jobs.errors import JobDeferError, JobPermanentError
from app.jobs.model import Job, JobStatus, JobTransition, on_job_defer, on_job_failure, on_job_success


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _job_from_row(row: dict[str, Any]) -> Job:
    return Job(
        id=_as_int(row.get("id")),
        status=JobStatus(str(row.get("status") or "").strip() or JobStatus.RUNNING.value),
        attempt=_as_int(row.get("attempt")),
        max_attempts=max(1, _as_int(row.get("max_attempts"), default=3)),
        run_after=_as_str(row.get("run_after")),
        last_error=_as_str(row.get("last_error")),
        locked_by=_as_str(row.get("locked_by")),
        locked_at=_as_str(row.get("locked_at")),
    )


async def _apply_transition(
    engine: AsyncEngine,
    *,
    job_id: int,
    worker_id: str,
    transition: JobTransition,
) -> bool:
    sql = """
UPDATE jobs
SET status=:status,
    attempt=:attempt,
    run_after=:run_after,
    last_error=:last_error,
    locked_by=:locked_by,
    locked_at=:locked_at,
    updated_at=:updated_at
WHERE id=:id AND status='running' AND locked_by=:worker_id;
""".strip()

    async def _op() -> bool:
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql(
                sql,
                {
                    "status": transition.status.value,
                    "attempt": int(transition.attempt),
                    "run_after": transition.run_after,
                    "last_error": transition.last_error,
                    "locked_by": transition.locked_by,
                    "locked_at": transition.locked_at,
                    "updated_at": transition.updated_at,
                    "id": int(job_id),
                    "worker_id": worker_id,
                },
            )
            return (result.rowcount or 0) == 1

    return await with_sqlite_busy_retry(_op)


async def execute_claimed_job(
    engine: AsyncEngine,
    dispatcher: JobDispatcher,
    *,
    job_row: dict[str, Any],
    worker_id: str,
    now: datetime | None = None,
) -> JobTransition | None:
    worker_id = worker_id.strip()
    if not worker_id:
        raise ValueError("worker_id is required")

    job = _job_from_row(job_row)
    if job.id <= 0:
        raise ValueError("job_row.id is required")

    now_dt = now or datetime.now(timezone.utc)

    try:
        await dispatcher.dispatch(job_row)
    except JobDeferError as exc:
        transition = on_job_defer(job, run_after=exc.run_after, error=f"{type(exc).__name__}: {exc}", now=now_dt)
    except JobPermanentError as exc:
        forced = Job(
            id=job.id,
            status=job.status,
            attempt=job.attempt,
            max_attempts=max(1, job.attempt + 1),
            run_after=job.run_after,
            last_error=job.last_error,
            locked_by=job.locked_by,
            locked_at=job.locked_at,
        )
        transition = on_job_failure(forced, error=f"{type(exc).__name__}: {exc}", now=now_dt)
    except ValueError as exc:
        msg = str(exc)
        if "Unknown job type" in msg:
            forced = Job(
                id=job.id,
                status=job.status,
                attempt=job.attempt,
                max_attempts=max(1, job.attempt + 1),
                run_after=job.run_after,
                last_error=job.last_error,
                locked_by=job.locked_by,
                locked_at=job.locked_at,
            )
            transition = on_job_failure(forced, error=f"{type(exc).__name__}: {exc}", now=now_dt)
        else:
            transition = on_job_failure(job, error=f"{type(exc).__name__}: {exc}", now=now_dt)
    except Exception as exc:
        if is_sqlite_busy_error(exc):
            delay_s = 2.0 + random.random() * 3.0
            run_after = iso_utc_ms(now_dt + timedelta(seconds=delay_s))
            transition = on_job_defer(job, run_after=run_after, error=f"{type(exc).__name__}: {exc}", now=now_dt)
        else:
            transition = on_job_failure(job, error=f"{type(exc).__name__}: {exc}", now=now_dt)
    else:
        transition = on_job_success(job, now=now_dt)

    ok = await _apply_transition(engine, job_id=job.id, worker_id=worker_id, transition=transition)
    if ok and transition.status in {JobStatus.FAILED, JobStatus.DLQ}:
        JOBS_FAILED_TOTAL.inc()
    return transition if ok else None
