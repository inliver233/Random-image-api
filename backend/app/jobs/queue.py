from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncEngine

from app.jobs.claim import (
    DEFAULT_LOCK_TTL_S,
    claim_next_job,
    claim_pending_job_by_id,
    renew_job_lock,
)


@runtime_checkable
class JobQueuePort(Protocol):
    """Job claim/lock port (SQLite today; Redis/NATS later without worker rewrite).

    Implementations must preserve claim semantics: exclusive running lock, lock TTL,
    priority order, and renew while the worker still holds the job.
    """

    backend: str

    async def claim_next(
        self,
        *,
        worker_id: str,
        lock_ttl_s: int = DEFAULT_LOCK_TTL_S,
        now: datetime | None = None,
    ) -> dict[str, Any] | None: ...

    async def claim_pending_by_id(
        self,
        *,
        job_id: int,
        worker_id: str,
        now: str,
    ) -> dict[str, Any] | None: ...

    async def renew_lock(
        self,
        *,
        job_id: int,
        worker_id: str,
        now: datetime | None = None,
    ) -> bool: ...


class SqliteJobQueue:
    """Default queue: SQLite jobs table via existing claim SQL (behavior unchanged)."""

    backend: str = "sqlite"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def claim_next(
        self,
        *,
        worker_id: str,
        lock_ttl_s: int = DEFAULT_LOCK_TTL_S,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        return await claim_next_job(
            self._engine,
            worker_id=worker_id,
            lock_ttl_s=int(lock_ttl_s),
            now=now,
        )

    async def claim_pending_by_id(
        self,
        *,
        job_id: int,
        worker_id: str,
        now: str,
    ) -> dict[str, Any] | None:
        return await claim_pending_job_by_id(
            self._engine,
            job_id=int(job_id),
            worker_id=worker_id,
            now=now,
        )

    async def renew_lock(
        self,
        *,
        job_id: int,
        worker_id: str,
        now: datetime | None = None,
    ) -> bool:
        return await renew_job_lock(
            self._engine,
            job_id=int(job_id),
            worker_id=worker_id,
            now=now,
        )


def build_job_queue(engine: AsyncEngine, *, backend: str = "sqlite") -> JobQueuePort:
    """Factory for job queue backends. Only sqlite is implemented; unknown → sqlite."""
    backend_norm = (backend or "sqlite").strip().lower()
    if backend_norm not in {"sqlite", "memory"}:
        # redis/nats reserved — fall back to sqlite until implemented.
        backend_norm = "sqlite"
    return SqliteJobQueue(engine)
