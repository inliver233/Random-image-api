from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logging import get_logger
from app.core.time import iso_utc_ms
from app.core.runtime_settings import set_runtime_setting
from app.db.session import with_sqlite_busy_retry

log = get_logger(__name__)

RANDOM_TOTALS_KEY = "stats.random.total"


def _as_nonneg_int(value: Any) -> int:
    try:
        i = int(value)
    except Exception:
        return 0
    return i if i > 0 else 0


async def load_persisted_random_totals(engine: AsyncEngine) -> dict[str, int]:
    sql = "SELECT value_json FROM runtime_settings WHERE key = ?;"

    async def _op() -> str | None:
        async with engine.connect() as conn:
            row = (await conn.exec_driver_sql(sql, (RANDOM_TOTALS_KEY,))).fetchone()
            return str(row[0]) if row is not None and row[0] is not None else None

    try:
        raw = await with_sqlite_busy_retry(_op)
    except Exception:
        return {"total_requests": 0, "total_ok": 0, "total_error": 0}

    if raw is None:
        return {"total_requests": 0, "total_ok": 0, "total_error": 0}

    try:
        data = json.loads(str(raw))
    except Exception:
        return {"total_requests": 0, "total_ok": 0, "total_error": 0}

    if not isinstance(data, dict):
        return {"total_requests": 0, "total_ok": 0, "total_error": 0}

    return {
        "total_requests": _as_nonneg_int(data.get("total_requests")),
        "total_ok": _as_nonneg_int(data.get("total_ok")),
        "total_error": _as_nonneg_int(data.get("total_error")),
    }


async def persist_random_totals(
    engine: AsyncEngine,
    *,
    total_requests: int,
    total_ok: int,
    total_error: int,
    source: str,
) -> None:
    value = {
        "total_requests": max(0, int(total_requests)),
        "total_ok": max(0, int(total_ok)),
        "total_error": max(0, int(total_error)),
        "updated_at": iso_utc_ms(),
        "source": str(source or "unknown"),
    }
    try:
        await set_runtime_setting(
            engine,
            key=RANDOM_TOTALS_KEY,
            value=value,
            description="persisted /random totals",
            updated_by=f"system:{source}",
        )
    except Exception as exc:
        try:
            log.warning("persist_random_totals_failed err=%s", f"{type(exc).__name__}: {exc}")
        except Exception:
            pass

