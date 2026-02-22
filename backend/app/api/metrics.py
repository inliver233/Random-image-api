from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from prometheus_client.exposition import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.admin.deps import get_admin_claims
from app.core.metrics import (
    METRICS_LAST_SCRAPE_SUCCESS,
    METRICS_SCRAPE_ERRORS_TOTAL,
    ensure_known_keys,
    set_jobs_status_counts,
    set_proxy_state_counts,
)
from app.core.time import iso_utc_ms

router = APIRouter()


async def _query_job_status_counts(engine: AsyncEngine) -> dict[str, int]:
    sql = "SELECT status, COUNT(*) AS c FROM jobs GROUP BY status"
    async with engine.connect() as conn:
        result = await conn.exec_driver_sql(sql)
        counts: dict[str, int] = {}
        for row in result.fetchall():
            status = str(row[0])
            counts[status] = int(row[1])
        return counts


async def _query_proxy_state_counts(engine: AsyncEngine) -> dict[str, int]:
    now = iso_utc_ms()
    sql = """
SELECT
  COUNT(*) AS total,
  SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled,
  SUM(CASE WHEN enabled = 1 AND blacklisted_until IS NOT NULL AND blacklisted_until > :now THEN 1 ELSE 0 END) AS blacklisted,
  SUM(CASE WHEN enabled = 1
           AND (blacklisted_until IS NULL OR blacklisted_until <= :now)
           AND last_ok_at IS NOT NULL
           AND (last_fail_at IS NULL OR last_ok_at >= last_fail_at)
      THEN 1 ELSE 0 END) AS healthy,
  SUM(CASE WHEN enabled = 1
           AND (blacklisted_until IS NULL OR blacklisted_until <= :now)
           AND (last_ok_at IS NULL OR (last_fail_at IS NOT NULL AND last_fail_at > last_ok_at))
      THEN 1 ELSE 0 END) AS unhealthy
FROM proxy_endpoints;
""".strip()
    async with engine.connect() as conn:
        result = await conn.exec_driver_sql(sql, {"now": now})
        row = result.fetchone()
        if row is None:
            return {"total": 0, "enabled": 0, "healthy": 0, "unhealthy": 0, "blacklisted": 0}
        return {
            "total": int(row[0] or 0),
            "enabled": int(row[1] or 0),
            "blacklisted": int(row[2] or 0),
            "healthy": int(row[3] or 0),
            "unhealthy": int(row[4] or 0),
        }


@router.get("/metrics")
async def metrics(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> Response:
    engine: AsyncEngine | None = getattr(request.app.state, "engine", None)
    if engine is not None:
        try:
            job_counts = await _query_job_status_counts(engine)
            set_jobs_status_counts(job_counts)

            proxy_counts = await _query_proxy_state_counts(engine)
            proxy_counts = ensure_known_keys(["total", "enabled", "healthy", "unhealthy", "blacklisted"], proxy_counts)
            set_proxy_state_counts(proxy_counts)

            METRICS_LAST_SCRAPE_SUCCESS.set(1)
        except Exception:
            METRICS_SCRAPE_ERRORS_TOTAL.inc()
            METRICS_LAST_SCRAPE_SUCCESS.set(0)

    content = generate_latest()
    return Response(content=content, media_type=CONTENT_TYPE_LATEST)

