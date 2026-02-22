from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.errors import ErrorCode, error_body
from app.core.request_id import get_or_create_request_id, set_request_id_header, set_request_id_on_state
from app.db.session import with_sqlite_busy_retry

router = APIRouter()

_JOB_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "paused",
    "canceled",
    "completed",
    "failed",
    "dlq",
)


def _ensure_sqlite_dir(engine: AsyncEngine) -> None:
    url = engine.url
    if url.get_backend_name() != "sqlite":
        return
    db_path = url.database
    if not db_path or db_path == ":memory:":
        return
    Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


async def _check_db(engine: AsyncEngine) -> bool:
    try:
        _ensure_sqlite_dir(engine)
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False


def _parse_iso_utc(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _query_worker_last_seen(engine: AsyncEngine) -> tuple[str | None, str]:
    sql = "SELECT value_json FROM runtime_settings WHERE key = ?"

    async def _op() -> str | None:
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql(sql, ("worker.last_seen_at",))
            row = result.fetchone()
            return str(row[0]) if row is not None else None

    try:
        raw = await with_sqlite_busy_retry(_op)
    except Exception:
        return None, "runtime_settings_unavailable"

    if raw is None:
        return None, "no_heartbeat"

    try:
        value = json.loads(raw)
    except Exception:
        return None, "invalid_json"

    if isinstance(value, dict):
        at = value.get("at")
        if isinstance(at, str):
            return at, "ok"
        return None, "invalid_value"

    if isinstance(value, str):
        return value, "ok"

    return None, "invalid_value"


async def _query_queue_status_counts(engine: AsyncEngine) -> tuple[dict[str, int] | None, str]:
    sql = "SELECT status, COUNT(*) AS c FROM jobs GROUP BY status"

    async def _op() -> dict[str, int]:
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql(sql)
            counts: dict[str, int] = {}
            for row in result.fetchall():
                counts[str(row[0])] = int(row[1])
            return counts

    try:
        counts = await with_sqlite_busy_retry(_op)
    except Exception:
        return None, "jobs_unavailable"

    for status in _JOB_STATUSES:
        counts.setdefault(status, 0)
    return counts, "ok"


@router.get("/healthz")
async def healthz(request: Request) -> Any:
    rid = get_or_create_request_id(request)
    set_request_id_on_state(request, rid)

    engine: AsyncEngine | None = getattr(request.app.state, "engine", None)
    db_ok = await _check_db(engine) if engine is not None else False

    if db_ok:
        try:
            stale_after_s = int((os.environ.get("WORKER_HEARTBEAT_STALE_SECONDS") or "60").strip() or "60")
        except Exception:
            stale_after_s = 60
        stale_after_s = max(1, min(int(stale_after_s), 24 * 60 * 60))

        worker_last_seen_at, worker_reason = await _query_worker_last_seen(engine)  # type: ignore[arg-type]
        worker_ok = False
        if worker_last_seen_at is not None:
            last_seen_dt = _parse_iso_utc(worker_last_seen_at)
            if last_seen_dt is None:
                worker_reason = "invalid_timestamp"
            else:
                worker_ok = datetime.now(timezone.utc) - last_seen_dt <= timedelta(seconds=stale_after_s)
                if not worker_ok:
                    worker_reason = "stale"

        queue_counts, queue_reason = await _query_queue_status_counts(engine)  # type: ignore[arg-type]
        queue_ok = queue_counts is not None

        resp = JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "db_ok": True,
                "worker_ok": worker_ok,
                "queue_ok": queue_ok,
                "worker": {
                    "last_seen_at": worker_last_seen_at,
                    "stale_after_s": stale_after_s,
                    "reason": worker_reason,
                },
                "queue": {
                    "counts": queue_counts or {},
                    "reason": queue_reason,
                },
                "request_id": rid,
            },
        )
    else:
        resp = JSONResponse(
            status_code=503,
            content=error_body(
                code=ErrorCode.INTERNAL_ERROR,
                message="Database unavailable",
                request_id=rid,
                details={"db_ok": False},
            ),
        )

    set_request_id_header(resp, rid)
    return resp
