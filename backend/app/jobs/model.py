from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from app.core.coerce import truncate_text
from app.core.redact import redact_text
from app.core.time import iso_utc_ms
from app.jobs.backoff import backoff_seconds


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELED = "canceled"
    COMPLETED = "completed"
    FAILED = "failed"
    DLQ = "dlq"


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    status: JobStatus
    attempt: int
    max_attempts: int
    run_after: str | None = None
    last_error: str | None = None
    locked_by: str | None = None
    locked_at: str | None = None


@dataclass(frozen=True, slots=True)
class JobTransition:
    status: JobStatus
    attempt: int
    run_after: str | None
    last_error: str | None
    locked_by: str | None
    locked_at: str | None
    updated_at: str


def on_job_success(job: Job, *, now: datetime | None = None) -> JobTransition:
    now_dt = now or datetime.now(timezone.utc)
    return JobTransition(
        status=JobStatus.COMPLETED,
        attempt=job.attempt,
        run_after=None,
        last_error=None,
        locked_by=None,
        locked_at=None,
        updated_at=iso_utc_ms(now_dt),
    )


def on_job_failure(job: Job, *, error: str, now: datetime | None = None) -> JobTransition:
    now_dt = now or datetime.now(timezone.utc)
    next_attempt = job.attempt + 1
    redacted_error = truncate_text(redact_text(error), max_len=2000)

    if next_attempt >= job.max_attempts:
        return JobTransition(
            status=JobStatus.DLQ,
            attempt=next_attempt,
            run_after=None,
            last_error=redacted_error,
            locked_by=None,
            locked_at=None,
            updated_at=iso_utc_ms(now_dt),
        )

    delay_s = backoff_seconds(next_attempt)
    run_after_dt = now_dt + timedelta(seconds=delay_s)
    return JobTransition(
        status=JobStatus.FAILED,
        attempt=next_attempt,
        run_after=iso_utc_ms(run_after_dt),
        last_error=redacted_error,
        locked_by=None,
        locked_at=None,
        updated_at=iso_utc_ms(now_dt),
    )


def on_job_defer(job: Job, *, run_after: str, error: str, now: datetime | None = None) -> JobTransition:
    now_dt = now or datetime.now(timezone.utc)
    run_after = (run_after or "").strip()
    if not run_after:
        raise ValueError("run_after is required")
    redacted_error = truncate_text(redact_text(error), max_len=2000)

    return JobTransition(
        status=JobStatus.FAILED,
        attempt=job.attempt,
        run_after=run_after,
        last_error=redacted_error,
        locked_by=None,
        locked_at=None,
        updated_at=iso_utc_ms(now_dt),
    )
