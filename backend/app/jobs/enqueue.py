from __future__ import annotations

import json

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.metrics import RANDOM_OPPORTUNISTIC_HYDRATE_ENQUEUED_TOTAL
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker, with_sqlite_busy_retry

OPPORTUNISTIC_HYDRATE_REF_TYPE = "opportunistic_hydrate"
OPPORTUNISTIC_HYDRATE_PRIORITY = -10


async def enqueue_opportunistic_hydrate_metadata(
    engine: AsyncEngine,
    *,
    illust_id: int,
    reason: str,
) -> int | None:
    if int(illust_id) <= 0:
        return None

    ref_id = str(int(illust_id))
    Session = create_sessionmaker(engine)

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
            await session.flush()
            await session.commit()
            return int(job.id)

    job_id = await with_sqlite_busy_retry(_op)
    if job_id is not None:
        RANDOM_OPPORTUNISTIC_HYDRATE_ENQUEUED_TOTAL.inc()
    return job_id
