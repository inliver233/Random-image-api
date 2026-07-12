from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.data_files import ensure_sqlite_parent_dir
from app.core.env_parse import parse_int_env
from app.core.errors import ErrorCode, error_body
from app.core.cf_api_proxy import load_cf_api_proxy_config_from_settings
from app.core.image_edge import load_image_edge_config_from_settings
from app.core.r2_prewarm import r2_prewarm_enabled, r2_prewarm_secret
from app.core.random_engine_client import engine_circuit_snapshot, random_engine_base_url
from app.core.request_id import get_or_create_request_id, set_request_id_header, set_request_id_on_state
from app.core.runtime_settings import worker_last_seen_from_value_json
from app.core.time import parse_iso_dt
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


async def _check_db(engine: AsyncEngine) -> bool:
    try:
        ensure_sqlite_parent_dir(engine.url)
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False


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

    # Distinguish invalid JSON vs missing/invalid value shape for health diagnostics.
    try:
        _ = json.loads(str(raw))
    except Exception:
        return None, "invalid_json"

    at = worker_last_seen_from_value_json(str(raw))
    if at is None:
        return None, "invalid_value"
    return at, "ok"


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
        stale_after_s = parse_int_env(
            "WORKER_HEARTBEAT_STALE_SECONDS",
            default=60,
            min_v=1,
            max_v=24 * 60 * 60,
        )

        worker_last_seen_at, worker_reason = await _query_worker_last_seen(engine)  # type: ignore[arg-type]
        worker_ok = False
        if worker_last_seen_at is not None:
            last_seen_dt = parse_iso_dt(worker_last_seen_at)
            if last_seen_dt is None:
                worker_reason = "invalid_timestamp"
            else:
                worker_ok = datetime.now(timezone.utc) - last_seen_dt <= timedelta(seconds=stale_after_s)
                if not worker_ok:
                    worker_reason = "stale"

        queue_counts, queue_reason = await _query_queue_status_counts(engine)  # type: ignore[arg-type]
        queue_ok = queue_counts is not None

        # Optional dual-stack readiness (config only — no outbound probes on /healthz).
        settings = getattr(request.app.state, "settings", None)
        edge_cfg = load_image_edge_config_from_settings(settings) if settings is not None else None
        cf_api_cfg = load_cf_api_proxy_config_from_settings(settings) if settings is not None else None
        engine_url = random_engine_base_url(settings) if settings is not None else None
        rl_backend = (
            str(getattr(settings, "public_api_key_rate_limit_backend", "memory") or "memory").lower()
            if settings is not None
            else "memory"
        )
        if rl_backend not in {"memory", "redis"}:
            rl_backend = "memory"
        redis_url_configured = bool(str(getattr(settings, "redis_url", "") or "").strip()) if settings is not None else False
        _limiter = getattr(request.app.state, "api_key_limiter", None)
        rl_active = str(
            getattr(_limiter, "active_backend", None)
            or getattr(_limiter, "backend", "memory")
            or "memory"
        ).strip().lower()
        if rl_active not in {"memory", "redis"}:
            # No limiter on app.state (minimal healthz harness) → fold config like before.
            rl_active = rl_backend if (rl_backend != "redis" or redis_url_configured) else "memory"
        job_queue_requested = (
            str(getattr(settings, "job_queue_backend", "sqlite") or "sqlite").strip().lower()
            if settings is not None
            else "sqlite"
        )
        if job_queue_requested not in {"sqlite", "memory"}:
            # Settings rejects redis/nats at boot; normalize any other label for honesty.
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
        modules = {
            "image_edge": {
                "enabled_flag": bool(getattr(settings, "image_edge_enabled", False)) if settings is not None else False,
                "ready": edge_cfg is not None,
                "base_url_count": len(edge_cfg.base_urls) if edge_cfg is not None else 0,
            },
            "cf_api_proxy": {
                "enabled_flag": bool(getattr(settings, "cf_api_proxy_enabled", False)) if settings is not None else False,
                "ready": bool(cf_api_cfg is not None and cf_api_cfg.ready),
                "base_url_count": len(cf_api_cfg.base_urls) if cf_api_cfg is not None else 0,
                "has_secret": bool((cf_api_cfg.secret or "").strip()) if cf_api_cfg is not None else False,
            },
            "r2_prewarm": {
                # Parity with admin /maintenance/r2-prewarm: ready requires secret.
                "enabled_flag": bool(getattr(settings, "r2_prewarm_enabled", False)) if settings is not None else False,
                "ready": (
                    bool(r2_prewarm_enabled(settings) and r2_prewarm_secret(settings))
                    if settings is not None
                    else False
                ),
                "url_configured": bool(str(getattr(settings, "r2_prewarm_url", "") or "").strip())
                if settings is not None
                else False,
                "secret_configured": bool(r2_prewarm_secret(settings)) if settings is not None else False,
            },
            "random_engine": {
                "url_configured": bool(engine_url),
                "enabled": bool(getattr(settings, "random_engine_enabled", False)) if settings is not None else False,
                "traffic_percent": int(getattr(settings, "random_engine_traffic_percent", 100) or 0)
                if settings is not None
                else 0,
                # Process dual-run circuit (local snapshot; no outbound engine probe on /healthz).
                "circuit": engine_circuit_snapshot(),
            },
            "api_key_rate_limit": {
                # No outbound Redis probe on /healthz. backend = active limiter label.
                "backend": rl_active,
                "requested": rl_backend,
                "redis_url_configured": redis_url_configured,
                "required": bool(getattr(settings, "public_api_key_required", False)) if settings is not None else False,
                "using_memory_fallback": rl_backend == "redis" and rl_active == "memory",
            },
            # Job claim port (sqlite/memory only; redis/nats rejected at settings load).
            "job_queue": {
                "backend": (
                    str(getattr(getattr(request.app.state, "job_queue", None), "backend", "sqlite") or "sqlite")
                ),
                "requested": job_queue_requested,
                "implemented": job_queue_requested in {"sqlite", "memory"},
            },
            # Catalog store dialect (sqlite default; postgres when DATABASE_URL is postgres*).
            "catalog": {
                "backend": (
                    str(getattr(getattr(request.app.state, "catalog_store", None), "backend", "sqlite") or "sqlite")
                ),
            },
            # Tag store dialect (sqlite default; separate from CatalogStore image rows).
            "tags": {
                "backend": (
                    str(getattr(getattr(request.app.state, "tag_store", None), "backend", "sqlite") or "sqlite")
                ),
            },
            # Anti-repeat short window (memory default; redis when ready).
            # backend = active store; requested = Settings.recent_dedup_backend (honesty).
            "recent_dedup": {
                "backend": recent_dedup_active,
                "requested": recent_dedup_requested,
                "using_memory_fallback": (
                    recent_dedup_requested == "redis" and recent_dedup_active == "memory"
                ),
            },
            # RandomService factory (default plan builder; swappable later).
            "random_service": {
                "backend": (
                    str(getattr(getattr(request.app.state, "random_service", None), "backend", "default") or "default")
                ),
            },
            # Python SQL ring-pick fallback (sqlite default).
            "random_pick": {
                "backend": (
                    str(getattr(getattr(request.app.state, "random_pick", None), "backend", "sqlite") or "sqlite")
                ),
            },
        }

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
                "modules": modules,
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
