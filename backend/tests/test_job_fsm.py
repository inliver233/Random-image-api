from __future__ import annotations

from datetime import datetime, timezone

from app.jobs.backoff import backoff_seconds
from app.jobs.model import Job, JobStatus, on_job_failure, on_job_success


def test_job_fsm_backoff_schedule() -> None:
    assert backoff_seconds(0) == 0
    assert backoff_seconds(1) == 5
    assert backoff_seconds(2) == 30
    assert backoff_seconds(3) == 120
    assert backoff_seconds(4) == 600
    assert backoff_seconds(5) == 1800
    assert backoff_seconds(6) >= 1800


def test_job_fsm_failure_transitions_to_failed_and_sets_run_after() -> None:
    now = datetime(2026, 2, 10, 0, 0, 0, tzinfo=timezone.utc)
    job = Job(id=1, status=JobStatus.RUNNING, attempt=0, max_attempts=3)
    t = on_job_failure(job, error="boom", now=now)
    assert t.status == JobStatus.FAILED
    assert t.attempt == 1
    assert t.run_after == "2026-02-10T00:00:05.000Z"
    assert t.locked_by is None
    assert t.locked_at is None


def test_job_fsm_failure_transitions_to_dlq_at_max_attempts() -> None:
    now = datetime(2026, 2, 10, 0, 0, 0, tzinfo=timezone.utc)
    job = Job(id=1, status=JobStatus.RUNNING, attempt=2, max_attempts=3)
    t = on_job_failure(job, error="boom", now=now)
    assert t.status == JobStatus.DLQ
    assert t.attempt == 3
    assert t.run_after is None


def test_job_fsm_success_transitions_to_completed() -> None:
    now = datetime(2026, 2, 10, 0, 0, 0, tzinfo=timezone.utc)
    job = Job(id=1, status=JobStatus.RUNNING, attempt=1, max_attempts=3)
    t = on_job_success(job, now=now)
    assert t.status == JobStatus.COMPLETED
    assert t.run_after is None
    assert t.last_error is None

