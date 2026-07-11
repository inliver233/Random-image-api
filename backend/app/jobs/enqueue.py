from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.metrics import RANDOM_OPPORTUNISTIC_HYDRATE_ENQUEUED_TOTAL
from app.jobs.queue import (
    OPPORTUNISTIC_HYDRATE_PRIORITY,
    OPPORTUNISTIC_HYDRATE_REF_TYPE,
    JobQueuePort,
    resolve_job_queue,
)

# Re-export constants for callers/tests that imported them from enqueue.
__all__ = [
    "OPPORTUNISTIC_HYDRATE_PRIORITY",
    "OPPORTUNISTIC_HYDRATE_REF_TYPE",
    "enqueue_opportunistic_hydrate_metadata",
]


async def enqueue_opportunistic_hydrate_metadata(
    engine: AsyncEngine,
    *,
    illust_id: int,
    reason: str,
    queue: JobQueuePort | None = None,
) -> int | None:
    """Best-effort enqueue via JobQueuePort (SQLite default; behavior unchanged)."""
    job_queue = resolve_job_queue(queue, engine)
    job_id = await job_queue.enqueue_opportunistic_hydrate(illust_id=int(illust_id), reason=reason)
    if job_id is not None:
        RANDOM_OPPORTUNISTIC_HYDRATE_ENQUEUED_TOTAL.inc()
    return job_id
