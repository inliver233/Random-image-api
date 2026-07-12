from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from prometheus_client.exposition import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.admin.deps import get_admin_claims
from app.core.cf_api_proxy import load_cf_api_proxy_config_from_settings
from app.core.image_edge import load_image_edge_config_from_settings
from app.core.metrics import (
    METRICS_LAST_SCRAPE_SUCCESS,
    METRICS_SCRAPE_ERRORS_TOTAL,
    ensure_known_keys,
    set_jobs_status_counts,
    set_modular_readiness_snapshot,
    set_proxy_state_counts,
    set_random_engine_circuit_snapshot,
)
from app.core.r2_prewarm import r2_prewarm_enabled, r2_prewarm_secret
from app.core.random_engine_client import engine_circuit_snapshot
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


def _modular_readiness_from_request(request: Request) -> dict[str, Any]:
    """Local modular readiness for /metrics (parity with /healthz modules.*; no secrets/probes)."""
    settings = getattr(request.app.state, "settings", None)
    edge_cfg = load_image_edge_config_from_settings(settings) if settings is not None else None
    cf_api_cfg = load_cf_api_proxy_config_from_settings(settings) if settings is not None else None

    rl_backend = (
        str(getattr(settings, "public_api_key_rate_limit_backend", "memory") or "memory").lower()
        if settings is not None
        else "memory"
    )
    if rl_backend not in {"memory", "redis"}:
        rl_backend = "memory"
    redis_url_configured = (
        bool(str(getattr(settings, "redis_url", "") or "").strip()) if settings is not None else False
    )
    _limiter = getattr(request.app.state, "api_key_limiter", None)
    rl_active = str(
        getattr(_limiter, "active_backend", None)
        or getattr(_limiter, "backend", "memory")
        or "memory"
    ).strip().lower()
    if rl_active not in {"memory", "redis"}:
        rl_active = rl_backend if (rl_backend != "redis" or redis_url_configured) else "memory"

    job_queue_requested = (
        str(getattr(settings, "job_queue_backend", "sqlite") or "sqlite").strip().lower()
        if settings is not None
        else "sqlite"
    )
    if job_queue_requested not in {"sqlite", "memory"}:
        job_queue_requested = "sqlite"

    recent_dedup_requested = (
        str(getattr(settings, "recent_dedup_backend", "memory") or "memory").strip().lower()
        if settings is not None
        else "memory"
    )
    if recent_dedup_requested not in {"memory", "redis"}:
        recent_dedup_requested = "memory"
    _recent = getattr(request.app.state, "recent_dedup", None)
    recent_dedup_active = str(
        getattr(_recent, "active_backend", None)
        or getattr(_recent, "backend", "memory")
        or "memory"
    ).strip().lower()
    if recent_dedup_active not in {"memory", "redis"}:
        recent_dedup_active = "memory"

    return {
        "image_edge": {
            "enabled": bool(getattr(settings, "image_edge_enabled", False)) if settings is not None else False,
            "ready": edge_cfg is not None,
            "base_url_count": len(edge_cfg.base_urls) if edge_cfg is not None else 0,
        },
        "cf_api_proxy": {
            "enabled": bool(getattr(settings, "cf_api_proxy_enabled", False)) if settings is not None else False,
            "ready": bool(cf_api_cfg is not None and cf_api_cfg.ready),
            "base_url_count": len(cf_api_cfg.base_urls) if cf_api_cfg is not None else 0,
        },
        "r2_prewarm": {
            "enabled": bool(getattr(settings, "r2_prewarm_enabled", False)) if settings is not None else False,
            "ready": (
                bool(r2_prewarm_enabled(settings) and r2_prewarm_secret(settings))
                if settings is not None
                else False
            ),
            "url_configured": (
                bool(str(getattr(settings, "r2_prewarm_url", "") or "").strip())
                if settings is not None
                else False
            ),
        },
        "api_key_rate_limit": {
            "required": bool(getattr(settings, "public_api_key_required", False)) if settings is not None else False,
            "using_memory_fallback": rl_backend == "redis" and rl_active == "memory",
            "redis_url_configured": redis_url_configured,
        },
        "job_queue": {
            "implemented": job_queue_requested in {"sqlite", "memory"},
        },
        "recent_dedup": {
            "using_memory_fallback": (
                recent_dedup_requested == "redis" and recent_dedup_active == "memory"
            ),
        },
    }


@router.get(
    "/metrics",
    summary="Prometheus metrics scrape",
    description=(
        "Admin-auth Prometheus text exposition. On each scrape refreshes local dual-run "
        "circuit gauges (`new_pixiv_random_engine_circuit_*`) and modular readiness "
        "(`new_pixiv_module_readiness`, `new_pixiv_module_base_url_count`) from process "
        "config — same honesty shapes as `/healthz` `modules.*` / public `/status.json` "
        "`data.*` (no secrets, no outbound engine/R2 probes). Also exports job status "
        "counts, proxy endpoint state counts, and delivery/random counters."
    ),
)
async def metrics(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> Response:
    # Local dual-run circuit gauges (no outbound engine probe; always refresh on scrape).
    set_random_engine_circuit_snapshot(engine_circuit_snapshot())
    # Local modular readiness (parity with /healthz modules.*; no secrets / no outbound probes).
    set_modular_readiness_snapshot(_modular_readiness_from_request(request))

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
