from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.jobs.claim import (
    DEFAULT_LOCK_TTL_S,
    claim_next_job,
    claim_pending_job_by_id,
    renew_job_lock,
)

OPPORTUNISTIC_HYDRATE_REF_TYPE = "opportunistic_hydrate"
OPPORTUNISTIC_HYDRATE_PRIORITY = -10


@runtime_checkable
class JobQueuePort(Protocol):
    """Job claim/lock + enqueue port (SQLite today; Redis/NATS later without worker rewrite).

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

    async def enqueue(
        self,
        *,
        type: str,
        payload_json: str,
        priority: int = 0,
        ref_type: str | None = None,
        ref_id: str | None = None,
        max_attempts: int = 3,
    ) -> int: ...

    async def enqueue_opportunistic_hydrate(
        self,
        *,
        illust_id: int,
        reason: str,
    ) -> int | None: ...


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

    async def enqueue(
        self,
        *,
        type: str,
        payload_json: str,
        priority: int = 0,
        ref_type: str | None = None,
        ref_id: str | None = None,
        max_attempts: int = 3,
    ) -> int:
        Session = create_sessionmaker(self._engine)

        async def _op() -> int:
            async with Session() as session:
                job = JobRow(
                    type=str(type),
                    status="pending",
                    priority=int(priority),
                    payload_json=str(payload_json),
                    ref_type=ref_type,
                    ref_id=ref_id,
                    max_attempts=int(max_attempts),
                )
                session.add(job)
                await session.flush()
                await session.commit()
                return int(job.id)

        return await with_sqlite_busy_retry(_op)

    async def enqueue_opportunistic_hydrate(
        self,
        *,
        illust_id: int,
        reason: str,
    ) -> int | None:
        """Insert hydrate_metadata job if no active opportunistic row for illust_id.

        Returns job id or None when skipped (invalid id / already pending|running / race).
        """
        if int(illust_id) <= 0:
            return None

        ref_id = str(int(illust_id))
        Session = create_sessionmaker(self._engine)
        payload_json = json.dumps(
            {"illust_id": int(illust_id), "reason": str(reason or "random").strip() or "random"},
            ensure_ascii=False,
            separators=(",", ":"),
        )

        async def _op() -> int | None:
            async with Session() as session:
                existing = await session.execute(
                    sa.select(JobRow.id).where(
                        JobRow.type == "hydrate_metadata",
                        JobRow.ref_type == OPPORTUNISTIC_HYDRATE_REF_TYPE,
                        JobRow.ref_id == ref_id,
                        JobRow.status.in_(("pending", "running")),
                    )
                )
                if existing.first() is not None:
                    return None

                job = JobRow(
                    type="hydrate_metadata",
                    status="pending",
                    priority=int(OPPORTUNISTIC_HYDRATE_PRIORITY),
                    payload_json=payload_json,
                    ref_type=OPPORTUNISTIC_HYDRATE_REF_TYPE,
                    ref_id=ref_id,
                )
                session.add(job)
                try:
                    await session.flush()
                    await session.commit()
                except IntegrityError:
                    # Concurrent enqueue of the same active opportunistic hydrate job.
                    await session.rollback()
                    return None
                return int(job.id)

        return await with_sqlite_busy_retry(_op)


def build_job_queue(engine: AsyncEngine, *, backend: str = "sqlite") -> JobQueuePort:
    """Factory for job queue backends. Only sqlite is implemented; unknown → sqlite."""
    backend_norm = (backend or "sqlite").strip().lower()
    if backend_norm not in {"sqlite", "memory"}:
        # redis/nats reserved — fall back to sqlite until implemented.
        backend_norm = "sqlite"
    return SqliteJobQueue(engine)
