from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import load_settings
from app.core.r2_prewarm import maybe_enqueue_r2_prewarm
from app.core.random_engine_sync import maybe_publish_engine_upserts
from app.core.time import iso_utc_ms
from app.db.catalog import CatalogStore, build_catalog_store
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.jobs.payload import parse_job_payload_object
from app.jobs.errors import JobPermanentError
from app.jobs.handlers.hydrate_metadata import build_hydrate_metadata_handler


def build_heal_url_handler(
    engine: AsyncEngine,
    *,
    transport: httpx.BaseTransport | None = None,
    catalog: CatalogStore | None = None,
) -> Any:
    catalog_store = catalog if catalog is not None else build_catalog_store(database_url=str(engine.url))
    hydrate = build_hydrate_metadata_handler(engine, transport=transport, catalog=catalog_store)
    Session = create_sessionmaker(engine)

    async def _handler(job: dict[str, Any]) -> None:
        payload_json = str(job.get("payload_json") or "")
        payload = parse_job_payload_object(payload_json)

        try:
            illust_id = int(payload.get("illust_id"))
        except Exception as exc:
            raise JobPermanentError("payload.illust_id is required") from exc
        if illust_id <= 0:
            raise JobPermanentError("payload.illust_id is required")

        await hydrate(job)

        now_iso = iso_utc_ms(datetime.now(timezone.utc))

        async def _op() -> list[int]:
            async with Session() as session:
                healed_ids = await catalog_store.heal_broken_images_for_illust(
                    session,
                    illust_id=int(illust_id),
                    now=now_iso,
                )
                if healed_ids:
                    await session.commit()
                return list(healed_ids or [])

        healed_ids = await with_sqlite_busy_retry(_op)
        # hydrate already published upserts; re-publish after status=3→1 so engine re-indexes.
        if healed_ids:
            settings = load_settings()
            await maybe_publish_engine_upserts(
                engine,
                image_ids=list(healed_ids),
                settings=settings,
            )
            await maybe_enqueue_r2_prewarm(image_ids=list(healed_ids), settings=settings)

    return _handler

