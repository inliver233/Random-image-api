from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.time import iso_utc_ms
from app.db.models.images import Image
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.jobs.errors import JobPermanentError
from app.jobs.handlers.hydrate_metadata import build_hydrate_metadata_handler


def _parse_payload(payload_json: str) -> dict[str, Any]:
    try:
        data = json.loads(payload_json)
    except Exception as exc:
        raise JobPermanentError("payload_json is not valid JSON") from exc
    if not isinstance(data, dict):
        raise JobPermanentError("payload_json must be an object")
    return data


def build_heal_url_handler(engine: AsyncEngine, *, transport: httpx.BaseTransport | None = None) -> Any:
    hydrate = build_hydrate_metadata_handler(engine, transport=transport)
    Session = create_sessionmaker(engine)

    async def _handler(job: dict[str, Any]) -> None:
        payload_json = str(job.get("payload_json") or "")
        payload = _parse_payload(payload_json)

        try:
            illust_id = int(payload.get("illust_id"))
        except Exception as exc:
            raise JobPermanentError("payload.illust_id is required") from exc
        if illust_id <= 0:
            raise JobPermanentError("payload.illust_id is required")

        await hydrate(job)

        now_iso = iso_utc_ms(datetime.now(timezone.utc))

        async def _op() -> None:
            async with Session() as session:
                await session.execute(
                    sa.update(Image)
                    .where(Image.illust_id == int(illust_id))
                    .where(Image.status == 3)
                    .values(
                        status=1,
                        last_ok_at=now_iso,
                        last_error_code=None,
                        last_error_msg=None,
                        updated_at=now_iso,
                    )
                )
                await session.commit()

        await with_sqlite_busy_retry(_op)

    return _handler

