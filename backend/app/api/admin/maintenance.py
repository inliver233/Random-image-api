from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.errors import ApiError, ErrorCode
from app.core.admin_json import admin_ok
from app.core.admin_request import load_json_object_optional, load_json_object, parse_bool, parse_int_in_range
from app.core.cf_api_proxy import load_cf_api_proxy_config_from_settings
from app.core.image_edge import load_image_edge_config_from_settings
from app.core.r2_prewarm import r2_prewarm_enabled, r2_prewarm_secret, r2_prewarm_url
from app.core.random_defaults import resolve_fail_cooldown_ms, resolve_r18_strict
from app.core.random_engine_client import (
    engine_circuit_snapshot,
    engine_filter_count,
    engine_health,
    engine_pick,
    random_engine_base_url,
)
from app.core.random_engine_pick import build_engine_filters, build_engine_pick_payload
from app.core.random_delivery import resolve_catalog_store
from app.core.random_engine_sync import push_engine_snapshot
from app.core.recommendation import quality_score
from app.core.random_request import parse_random_filters
from app.core.request_id import get_or_create_request_id
from app.core.runtime_config_cache import resolve_runtime_for_request
from app.db.random_pick_port import resolve_random_pick
from app.db.request_logs_cleanup import (
    DEFAULT_REQUEST_LOGS_CHUNK_SIZE,
    DEFAULT_REQUEST_LOGS_KEEP_DAYS,
    DEFAULT_REQUEST_LOGS_MAX_DELETE_ROWS,
    cleanup_request_logs,
    preview_request_logs_cleanup,
)
from app.db.session import resolve_sessionmaker

router = APIRouter()



async def _load_cleanup_request_logs_json(request: Request) -> dict[str, Any]:
    data = await load_json_object(request)

    keep_days = parse_int_in_range(
        data.get("keep_days", DEFAULT_REQUEST_LOGS_KEEP_DAYS),
        field="keep_days",
        min_value=0,
        max_value=36500,
    )
    max_delete_rows = parse_int_in_range(
        data.get("max_delete_rows", DEFAULT_REQUEST_LOGS_MAX_DELETE_ROWS),
        field="max_delete_rows",
        min_value=1,
        max_value=10_000_000,
    )
    chunk_size = parse_int_in_range(
        data.get("chunk_size", DEFAULT_REQUEST_LOGS_CHUNK_SIZE),
        field="chunk_size",
        min_value=1,
        max_value=100_000,
    )
    dry_run = parse_bool(data.get("dry_run"), default=False)

    return {
        "keep_days": int(keep_days),
        "max_delete_rows": int(max_delete_rows),
        "chunk_size": int(chunk_size),
        "dry_run": bool(dry_run),
    }


@router.post(
    "/maintenance/request-logs/cleanup",
    summary="Cleanup request logs",
    description=(
        "Delete or dry-run purge of old `request_logs` rows. Body: `keep_days`, "
        "`max_delete_rows`, `chunk_size`, `dry_run`. Returns cutoff + deleted/would_delete "
        "counts and `has_more` when more rows remain past the batch cap."
    ),
)
async def cleanup_request_logs_endpoint(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    cfg = await _load_cleanup_request_logs_json(request)

    engine = request.app.state.engine
    if bool(cfg["dry_run"]):
        preview = await preview_request_logs_cleanup(
            engine,
            keep_days=int(cfg["keep_days"]),
            max_delete_rows=int(cfg["max_delete_rows"]),
        )
        return admin_ok(request, payload={"dry_run": True,
            "cutoff": preview.cutoff,
            "would_delete": int(preview.would_delete),
            "has_more": bool(preview.has_more)}, request_id=rid)

    result = await cleanup_request_logs(
        engine,
        keep_days=int(cfg["keep_days"]),
        max_delete_rows=int(cfg["max_delete_rows"]),
        chunk_size=int(cfg["chunk_size"]),
    )
    return admin_ok(request, payload={"dry_run": False,
        "cutoff": result.cutoff,
        "deleted": int(result.deleted),
        "has_more": bool(result.has_more)}, request_id=rid)


@router.get(
    "/maintenance/image-edge",
    summary="Image Edge readiness status",
    description=(
        "Read-only Image Edge config status (never returns secrets). "
        "Ops surface for Phase 2 cutover: whether signed CF edge URLs can be minted "
        "(`enabled_flag`, `ready`, `base_urls`/`base_url_count`, `has_secret`, `missing`). "
        "Deploy / multi-region 403 POC remains outside this process. "
        "Parity with `/healthz` `modules.image_edge` and public `/status.json` `data.image_edge`."
    ),
)
async def image_edge_status(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    settings = getattr(request.app.state, "settings", None)
    engine = getattr(request.app.state, "engine", None)
    try:
        from app.core.cf_pool_overlay import ensure_overlay_fresh, get_image_overlay_bases

        await ensure_overlay_fresh(engine, force=True)
        rt_bases = get_image_overlay_bases()
    except Exception:
        rt_bases = []
    flag_enabled = bool(getattr(settings, "image_edge_enabled", False)) if settings is not None else False
    raw_bases = list(getattr(settings, "image_edge_base_urls", None) or []) if settings is not None else []
    secret = str(getattr(settings, "image_edge_secret", "") or "").strip() if settings is not None else ""
    secret_previous = (
        str(getattr(settings, "image_edge_secret_previous", "") or "").strip() if settings is not None else ""
    )
    ttl = int(getattr(settings, "image_edge_sign_ttl_seconds", 604800) or 604800) if settings is not None else 604800
    cfg = load_image_edge_config_from_settings(settings) if settings is not None else None
    ready = cfg is not None
    merged_count = len(cfg.base_urls) if cfg is not None else max(len(raw_bases), len(rt_bases))
    missing: list[str] = []
    if not flag_enabled:
        missing.append("IMAGE_EDGE_ENABLED")
    if not secret:
        missing.append("IMAGE_EDGE_SECRET")
    # Bases may come from env CSV and/or runtime overlay (register/deploy).
    if not raw_bases and not rt_bases and not (cfg is not None and cfg.base_urls):
        missing.append("IMAGE_EDGE_BASE_URLS")
    return admin_ok(
        request,
        payload={
            "enabled_flag": flag_enabled,
            "ready": ready,
            "base_urls": list(cfg.base_urls) if cfg is not None else list(raw_bases),
            "base_url_count": len(cfg.base_urls) if cfg is not None else len(raw_bases),
            "env_base_url_count": len(raw_bases),
            "runtime_base_url_count": len(rt_bases),
            "merged_base_url_count": merged_count,
            "sign_ttl_seconds": int(cfg.sign_ttl_seconds) if cfg is not None else int(ttl),
            "has_secret": bool(secret),
            "has_secret_previous": bool(secret_previous) and secret_previous != secret,
            "missing": missing,
        },
        request_id=rid,
    )


@router.get(
    "/maintenance/cf-api-proxy",
    summary="CF API proxy readiness status",
    description=(
        "Read-only CF API egress pool status (never returns secrets). "
        "Ops surface for Phase 5: whether hydrate/OAuth prefer CF Worker egress "
        "(`enabled_flag`, `ready`, `base_urls`/`base_url_count`, `has_secret`, `missing`). "
        "Worker `PROXY_SECRET` is fail-closed; BFF secret required for ready. "
        "Parity with `/healthz` `modules.cf_api_proxy` and public `/status.json` `data.cf_api_proxy`."
    ),
)
async def cf_api_proxy_status(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    settings = getattr(request.app.state, "settings", None)
    engine = getattr(request.app.state, "engine", None)
    try:
        from app.core.cf_pool_overlay import ensure_overlay_fresh, get_api_overlay_bases

        await ensure_overlay_fresh(engine, force=True)
        rt_bases = get_api_overlay_bases()
    except Exception:
        rt_bases = []
    flag_enabled = bool(getattr(settings, "cf_api_proxy_enabled", False)) if settings is not None else False
    raw_bases = list(getattr(settings, "cf_api_proxy_base_urls", None) or []) if settings is not None else []
    secret = str(getattr(settings, "cf_api_proxy_secret", "") or "").strip() if settings is not None else ""
    cfg = load_cf_api_proxy_config_from_settings(settings) if settings is not None else None
    ready = bool(cfg is not None and cfg.ready)
    merged_count = len(cfg.base_urls) if cfg is not None else max(len(raw_bases), len(rt_bases))
    missing: list[str] = []
    if not flag_enabled:
        missing.append("CF_API_PROXY_ENABLED")
    # Bases may come from env CSV and/or runtime overlay (register/deploy).
    if not raw_bases and not rt_bases and not (cfg is not None and cfg.base_urls):
        missing.append("CF_API_PROXY_BASE_URLS")
    # Worker PROXY_SECRET is fail-closed; BFF secret required for ready.
    if not secret:
        missing.append("CF_API_PROXY_SECRET")
    return admin_ok(
        request,
        payload={
            "enabled_flag": flag_enabled,
            "ready": ready,
            "base_urls": list(cfg.base_urls) if cfg is not None else list(raw_bases),
            "base_url_count": len(cfg.base_urls) if cfg is not None else len(raw_bases),
            "env_base_url_count": len(raw_bases),
            "runtime_base_url_count": len(rt_bases),
            "merged_base_url_count": merged_count,
            "has_secret": bool(secret),
            "missing": missing,
        },
        request_id=rid,
    )


@router.get(
    "/maintenance/r2-prewarm",
    summary="R2 prewarm readiness status",
    description=(
        "Read-only R2 prewarm webhook status (never returns full secrets). "
        "BFF maps catalog image_ids → pximg paths, then POSTs Worker "
        "`POST /v1/prewarm` with `{paths}` + `X-Prewarm-Secret` after "
        "hydrate/import/heal when enabled. Returns `enabled_flag`/`ready`/"
        "`url_configured`/`secret_configured`/`url_preview`/`missing`. "
        "Worker R2 binding / R2_MODE is separate (ops). "
        "Parity with `/healthz` `modules.r2_prewarm` and public `/status.json` `data.r2_prewarm`."
    ),
)
async def r2_prewarm_status(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    settings = getattr(request.app.state, "settings", None)
    flag_enabled = bool(getattr(settings, "r2_prewarm_enabled", False)) if settings is not None else False
    raw_url = str(getattr(settings, "r2_prewarm_url", "") or "").strip() if settings is not None else ""
    ready = r2_prewarm_enabled(settings) if settings is not None else False
    url = r2_prewarm_url(settings) if settings is not None else None
    secret_configured = bool(r2_prewarm_secret(settings)) if settings is not None else False
    missing: list[str] = []
    if not flag_enabled:
        missing.append("R2_PREWARM_ENABLED")
    if not raw_url:
        missing.append("R2_PREWARM_URL")
    # Always list secret when unset (parity with image_edge / cf_api_proxy missing[]).
    if not secret_configured:
        missing.append("R2_PREWARM_SECRET|IMAGE_EDGE_SECRET")
    # Never return the full URL if it embeds credentials; only host-ish preview.
    url_preview = ""
    if url:
        try:
            from urllib.parse import urlparse

            p = urlparse(url)
            url_preview = f"{p.scheme}://{p.netloc}" if p.netloc else url[:48]
        except Exception:
            url_preview = url[:48]
    return admin_ok(
        request,
        payload={
            "enabled_flag": flag_enabled,
            "ready": ready and secret_configured,
            "url_configured": bool(raw_url),
            "secret_configured": secret_configured,
            "payload_shape": "paths",
            "url_preview": url_preview,
            "missing": missing,
        },
        request_id=rid,
    )


@router.get(
    "/maintenance/api-key-rate-limit",
    summary="API key rate-limit readiness status",
    description=(
        "Read-only public API key rate-limit backend status (never returns Redis URL). "
        "Default backend is process-local memory. Redis is optional via "
        "`PUBLIC_API_KEY_RATE_LIMIT_BACKEND=redis` + `REDIS_URL` (fail-open to memory). "
        "Returns `required`/`rpm`/`burst`/`configured_backend`/`active_backend`/"
        "`redis_url_configured`/`using_memory_fallback`. "
        "Parity with `/healthz` `modules.api_key_rate_limit` and public "
        "`/status.json` `data.api_key_rate_limit`."
    ),
)
async def api_key_rate_limit_status(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    settings = getattr(request.app.state, "settings", None)
    required = bool(getattr(settings, "public_api_key_required", False)) if settings is not None else False
    rpm = int(getattr(settings, "public_api_key_rpm", 0) or 0) if settings is not None else 0
    burst = int(getattr(settings, "public_api_key_burst", 0) or 0) if settings is not None else 0
    cfg_backend = (
        str(getattr(settings, "public_api_key_rate_limit_backend", "memory") or "memory").lower()
        if settings is not None
        else "memory"
    )
    if cfg_backend not in {"memory", "redis"}:
        cfg_backend = "memory"
    redis_url_configured = (
        bool(str(getattr(settings, "redis_url", "") or "").strip()) if settings is not None else False
    )
    limiter = getattr(request.app.state, "api_key_limiter", None)
    # Prefer active_backend (runtime fail-open) over static backend label on Redis wrappers.
    if limiter is not None:
        active_backend = str(
            getattr(limiter, "active_backend", None) or getattr(limiter, "backend", "memory") or "memory"
        ).lower()
    else:
        active_backend = "memory"
    if active_backend not in {"memory", "redis"}:
        active_backend = "memory"
    return admin_ok(
        request,
        payload={
            "required": required,
            "rpm": rpm,
            "burst": burst,
            "configured_backend": cfg_backend,
            "active_backend": active_backend,
            "redis_url_configured": redis_url_configured,
            # True when redis was requested but process is on memory (missing URL / import / connect).
            "using_memory_fallback": cfg_backend == "redis" and active_backend == "memory",
        },
        request_id=rid,
    )


@router.get(
    "/maintenance/modular-ports",
    summary="Modular ports status",
    description=(
        "Read-only Phase-4 port backends (no secrets, no outbound probes). "
        "Returns active labels for `catalog`, `tags`, `random_service`, `random_pick`, "
        "plus honesty for `job_queue` (`backend`/`requested`/`implemented` — redis/nats "
        "fail at settings load) and `recent_dedup` "
        "(`configured_backend`/`active_backend`/`redis_url_configured`/`using_memory_fallback`). "
        "Parity with `/healthz` `modules.*` and public `/status.json` `data.*` port chips."
    ),
)
async def modular_ports_status(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    settings = getattr(request.app.state, "settings", None)
    catalog = getattr(request.app.state, "catalog_store", None)
    tag_store = getattr(request.app.state, "tag_store", None)
    recent = getattr(request.app.state, "recent_dedup", None)
    catalog_backend = str(getattr(catalog, "backend", "sqlite") or "sqlite")
    tag_backend = str(getattr(tag_store, "backend", "sqlite") or "sqlite")
    recent_active = str(
        getattr(recent, "active_backend", None) or getattr(recent, "backend", "memory") or "memory"
    ).lower()
    if recent_active not in {"memory", "redis"}:
        recent_active = "memory"
    recent_cfg = (
        str(getattr(settings, "recent_dedup_backend", "memory") or "memory").lower()
        if settings is not None
        else "memory"
    )
    if recent_cfg not in {"memory", "redis"}:
        recent_cfg = "memory"
    redis_url_configured = (
        bool(str(getattr(settings, "redis_url", "") or "").strip()) if settings is not None else False
    )
    job_requested = (
        str(getattr(settings, "job_queue_backend", "sqlite") or "sqlite").strip().lower()
        if settings is not None
        else "sqlite"
    )
    if job_requested not in {"sqlite", "memory"}:
        # Settings rejects redis/nats at boot; normalize any other label for honesty.
        job_requested = "sqlite"
    job_queue = getattr(request.app.state, "job_queue", None)
    job_queue_backend = str(getattr(job_queue, "backend", "sqlite") or "sqlite")
    job_queue_implemented = job_requested in {"sqlite", "memory"}
    random_service = getattr(request.app.state, "random_service", None)
    random_backend = str(getattr(random_service, "backend", "default") or "default")
    random_pick = getattr(request.app.state, "random_pick", None)
    random_pick_backend = str(getattr(random_pick, "backend", "sqlite") or "sqlite")
    return admin_ok(
        request,
        payload={
            "catalog": {
                "backend": catalog_backend,
            },
            "tags": {
                "backend": tag_backend,
            },
            "job_queue": {
                # Active port from app.state (sqlite/memory only; redis/nats fail at settings load).
                "backend": job_queue_backend,
                "requested": job_requested,
                "implemented": job_queue_implemented,
            },
            "recent_dedup": {
                "configured_backend": recent_cfg,
                "active_backend": recent_active,
                "redis_url_configured": redis_url_configured,
                # True when redis requested but factory fell back (missing REDIS_URL).
                "using_memory_fallback": recent_cfg == "redis" and recent_active == "memory",
            },
            "random_service": {
                "backend": random_backend,
            },
            "random_pick": {
                "backend": random_pick_backend,
            },
        },
        request_id=rid,
    )


@router.get(
    "/maintenance/random-engine",
    summary="Random engine dual-run status",
    description=(
        "Admin dual-run cutover surface: local config (`enabled`, `url`, `traffic_percent`, "
        "`timeout_ms`) plus process circuit snapshot and optional outbound engine `/health` "
        "probe. `circuit` mirrors public `/status` / `/healthz` dual-run honesty "
        "(closed/half_open/open; fail-open to Python when open). "
        "`ready_for_traffic` requires enabled + traffic>0 + healthy + non-empty index. "
        "`cutover_warning` is operator-facing English (FE may localize)."
    ),
)
async def random_engine_status(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    settings = getattr(request.app.state, "settings", None)
    base = random_engine_base_url(settings) if settings is not None else None
    enabled = bool(getattr(settings, "random_engine_enabled", False)) if settings is not None else False
    traffic_percent = (
        int(getattr(settings, "random_engine_traffic_percent", 100) or 0) if settings is not None else 0
    )
    if traffic_percent < 0:
        traffic_percent = 0
    if traffic_percent > 100:
        traffic_percent = 100
    timeout_ms = int(getattr(settings, "random_engine_timeout_ms", 800) or 800) if settings is not None else 800
    # Process dual-run circuit (fail-open to Python pick when open).
    circuit = engine_circuit_snapshot()
    payload: dict[str, Any] = {
        "enabled": enabled,
        "url": base or "",
        "traffic_percent": traffic_percent,
        "timeout_ms": timeout_ms,
        "healthy": False,
        "health": None,
        "index_size": None,
        "index_empty": None,
        "ready_for_traffic": False,
        "cutover_warning": None,
        "circuit": circuit,
    }
    if not base:
        if enabled:
            payload["cutover_warning"] = "RANDOM_ENGINE_URL not configured"
        return admin_ok(request, payload=payload, request_id=rid)
    client = getattr(request.app.state, "httpx_client", None)
    if client is None:
        if enabled:
            payload["cutover_warning"] = "HTTP client unavailable"
        return admin_ok(request, payload=payload, request_id=rid)
    health = await engine_health(client, base, timeout_s=1.0)
    payload["healthy"] = health is not None
    payload["health"] = health
    index_size: int | None = None
    if isinstance(health, dict) and health.get("index_size") is not None:
        try:
            index_size = int(health.get("index_size"))
        except Exception:
            index_size = None
    payload["index_size"] = index_size
    if index_size is None:
        payload["index_empty"] = None if health is None else False
    else:
        payload["index_empty"] = index_size <= 0
    # Safe progressive cutover: dual-run flag on, traffic > 0, healthy, non-empty index.
    payload["ready_for_traffic"] = bool(
        enabled and traffic_percent > 0 and health is not None and index_size is not None and index_size > 0
    )
    if enabled and traffic_percent > 0:
        if health is None:
            payload["cutover_warning"] = "engine unreachable while dual-run traffic enabled"
        elif index_size is not None and index_size <= 0:
            payload["cutover_warning"] = "engine index empty — push snapshot before cutover"
        elif str(circuit.get("state") or "") == "open":
            payload["cutover_warning"] = (
                f"dual-run circuit open (~{float(circuit.get('open_remaining_s') or 0):.0f}s); picks fail-open to Python"
            )
    elif enabled and traffic_percent <= 0:
        payload["cutover_warning"] = "traffic_percent=0 (engine not receiving picks)"
    return admin_ok(request, payload=payload, request_id=rid)


@router.post(
    "/maintenance/random-engine/snapshot",
    summary="Push random engine snapshot",
    description=(
        "Push catalog snapshot (enabled images + tags) to the Go random engine. "
        "Requires `RANDOM_ENGINE_URL`. Optional body `limit` caps rows for dry probes. "
        "Uses process `catalog_store` / `tag_store` ports when present. Returns revision "
        "and engine ack payload, or 502 on upstream failure."
    ),
)
async def random_engine_push_snapshot(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    settings = getattr(request.app.state, "settings", None)
    base = random_engine_base_url(settings) if settings is not None else None
    if not base:
        raise ApiError(
            code=ErrorCode.BAD_REQUEST,
            message="RANDOM_ENGINE_URL not configured",
            status_code=400,
        )
    client = getattr(request.app.state, "httpx_client", None)
    if client is None:
        raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="HTTP client unavailable", status_code=500)

    limit: int | None = None
    body = await load_json_object_optional(request)
    if body.get("limit") is not None:
        try:
            n = int(body["limit"])
            if n >= 1:
                limit = n
        except Exception:
            limit = None

    revision = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    result = await push_engine_snapshot(
        request.app.state.engine,
        base_url=base,
        client=client,
        revision=revision,
        limit=limit,
        timeout_s=120.0,
        catalog=getattr(request.app.state, "catalog_store", None),
        tag_store=getattr(request.app.state, "tag_store", None),
        settings=settings,
    )
    if result is None:
        raise ApiError(
            code=ErrorCode.UPSTREAM_STREAM_ERROR,
            message="random-engine snapshot failed",
            status_code=502,
        )
    return admin_ok(request, payload={"revision": revision,
        "engine": result}, request_id=rid)


def _coerce_filter_int(raw: Any, *, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return int(default)


def _coerce_tag_list(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else None
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            s = str(item or "").strip()
            if s:
                out.append(s)
        return out or None
    return None


@router.post(
    "/maintenance/random-engine/compare-filters",
    summary="Compare random-engine filter cardinality (+ optional seeded pick probe)",
    description=(
        "Dual-run gate: SQLite filter cardinality vs Go engine index. Optional body fields "
        "mirror public `/random` query params (r18, tags, mins, …). Defaults = safe r18=0. "
        "Returns `match`/`python_filtered`/`engine_filtered`/`delta` plus engine index meta. "
        "Also runs a seeded strategy=random pick probe: engine pick id must exist in the "
        "catalog under the same filters (membership / index freshness). This is stronger "
        "than cardinality alone but still not a quality-score equality check (sampling)."
    ),
)
async def random_engine_compare_filters(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    settings = getattr(request.app.state, "settings", None)
    base = random_engine_base_url(settings) if settings is not None else None
    if not base:
        raise ApiError(
            code=ErrorCode.BAD_REQUEST,
            message="RANDOM_ENGINE_URL not configured",
            status_code=400,
        )
    client = getattr(request.app.state, "httpx_client", None)
    if client is None:
        raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="HTTP client unavailable", status_code=500)

    body = await load_json_object_optional(request)
    # Accept either nested filters or flat public-style fields.
    flat = body.get("filters") if isinstance(body.get("filters"), dict) else body
    if not isinstance(flat, dict):
        flat = {}

    def _opt_str(key: str) -> str | None:
        v = flat.get(key)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    filters = parse_random_filters(
        format="json",
        redirect=0,
        seed=None,
        r18=_coerce_filter_int(flat.get("r18"), default=0),
        ai_type=str(flat.get("ai_type") or "any"),
        illust_type=str(flat.get("illust_type") or "any"),
        orientation=str(flat.get("orientation") or "any"),
        layout=flat.get("layout"),
        adaptive=0,
        pixiv_cat=0,
        pximg_mirror_host=None,
        min_width=_coerce_filter_int(flat.get("min_width"), default=0),
        min_height=_coerce_filter_int(flat.get("min_height"), default=0),
        min_pixels=_coerce_filter_int(flat.get("min_pixels"), default=0),
        min_bookmarks=_coerce_filter_int(flat.get("min_bookmarks"), default=0),
        min_views=_coerce_filter_int(flat.get("min_views"), default=0),
        min_comments=_coerce_filter_int(flat.get("min_comments"), default=0),
        included_tags=_coerce_tag_list(flat.get("included_tags")),
        excluded_tags=_coerce_tag_list(flat.get("excluded_tags")),
        user_id=flat.get("user_id"),
        illust_id=flat.get("illust_id"),
        created_from=_opt_str("created_from"),
        created_to=_opt_str("created_to"),
        query_params={},
        headers={},
    )

    runtime = await resolve_runtime_for_request(request, request.app.state.engine)
    random_defaults = runtime.random_defaults if isinstance(runtime.random_defaults, dict) else {}
    r18_strict_resolved = resolve_r18_strict(None, random_defaults)
    r18_strict = int(r18_strict_resolved.value)
    _, _, fail_cooldown_before = resolve_fail_cooldown_ms(random_defaults)
    orientation_code = filters.orientation_map[filters.layout_norm]

    engine_filters = build_engine_filters(
        r18=int(filters.r18),
        r18_strict=int(r18_strict),
        ai_type_raw=filters.ai_type_raw,
        ai_type_i=filters.ai_type_i,
        illust_type_i=filters.illust_type_i,
        orientation_code=orientation_code,
        min_width_i=int(filters.min_width_i),
        min_height_i=int(filters.min_height_i),
        min_pixels_i=int(filters.min_pixels_i),
        min_bookmarks_i=int(filters.min_bookmarks_i),
        min_views_i=int(filters.min_views_i),
        min_comments_i=int(filters.min_comments_i),
        included=filters.included,
        excluded=filters.excluded,
        exclude_image_ids=[],
        user_id=filters.user_id,
        illust_id=filters.illust_id,
        created_from_norm=filters.created_from_norm,
        created_to_norm=filters.created_to_norm,
        fail_cooldown_before=fail_cooldown_before,
    )

    pick_port = resolve_random_pick(getattr(request.app.state, "random_pick", None))
    catalog = resolve_catalog_store(getattr(request.app.state, "catalog_store", None))
    pick_kwargs = {
        "r18": int(filters.r18),
        "r18_strict": bool(r18_strict),
        "orientation": orientation_code,
        "ai_type": filters.ai_type_i,
        "illust_type": filters.illust_type_i,
        "min_width": int(filters.min_width_i),
        "min_height": int(filters.min_height_i),
        "min_pixels": int(filters.min_pixels_i),
        "min_bookmarks": int(filters.min_bookmarks_i),
        "min_views": int(filters.min_views_i),
        "min_comments": int(filters.min_comments_i),
        "included_tags": filters.included,
        "excluded_tags": filters.excluded,
        "user_id": filters.user_id,
        "illust_id": filters.illust_id,
        "created_from": filters.created_from_norm,
        "created_to": filters.created_to_norm,
        "exclude_image_ids": None,
        "fail_cooldown_before": fail_cooldown_before,
    }
    Session = resolve_sessionmaker(request)
    async with Session() as session:
        python_count = await pick_port.count_candidates(session, **pick_kwargs)

    engine_result = await engine_filter_count(
        client, base, filters=engine_filters, timeout_s=5.0, settings=settings
    )
    if engine_result is None:
        raise ApiError(
            code=ErrorCode.UPSTREAM_STREAM_ERROR,
            message="random-engine filter-count failed",
            status_code=502,
        )
    try:
        engine_count = int(engine_result.get("filtered") or 0)
    except Exception:
        engine_count = 0
    try:
        engine_index = int(engine_result.get("index_size") or 0)
    except Exception:
        engine_index = 0
    revision = str(engine_result.get("revision") or "")

    delta = int(python_count) - int(engine_count)
    cardinality_match = delta == 0

    # Seeded random pick probe: engine id must be present in catalog (index freshness / membership).
    probe_seed = "compare-filters-probe-v1"
    pick_payload = build_engine_pick_payload(
        filters=engine_filters,
        strategy="random",
        quality=None,
        seed=probe_seed,
        limit=1,
        debug=False,
    )
    engine_pick_body = await engine_pick(
        client, base, payload=pick_payload, timeout_s=5.0, settings=settings
    )
    pick_probe: dict[str, Any] = {
        "seed": probe_seed,
        "strategy": "random",
        "engine_status": None,
        "engine_image_id": None,
        "in_catalog": None,
        "python_quality_score": None,
        "ok": False,
        "detail": "engine_pick_unavailable",
    }
    if isinstance(engine_pick_body, dict):
        pick_probe["engine_status"] = str(engine_pick_body.get("code") or "")
        items = engine_pick_body.get("items")
        first = items[0] if isinstance(items, list) and items else None
        if isinstance(first, dict) and first.get("id") is not None:
            try:
                engine_image_id = int(first["id"])
            except Exception:
                engine_image_id = None
            pick_probe["engine_image_id"] = engine_image_id
            if engine_image_id is not None:
                async with Session() as session:
                    row = await catalog.get_image_by_id(session, image_id=int(engine_image_id))
                if row is None:
                    pick_probe["in_catalog"] = False
                    pick_probe["detail"] = "engine_id_missing_in_catalog"
                else:
                    pick_probe["in_catalog"] = True
                    try:
                        pick_probe["python_quality_score"] = float(quality_score(row))
                    except Exception:
                        pick_probe["python_quality_score"] = None
                    pick_probe["ok"] = True
                    pick_probe["detail"] = "engine_id_in_catalog"
        elif str(engine_pick_body.get("code") or "") in {"INDEX_NOT_READY", "NO_MATCH"}:
            pick_probe["detail"] = str(engine_pick_body.get("code") or "no_items")
            # Empty index / no match is ok for probe when cardinality also empty.
            pick_probe["ok"] = int(engine_count) == 0 and int(python_count) == 0

    match = bool(cardinality_match) and bool(pick_probe.get("ok"))
    return admin_ok(
        request,
        payload={
            "match": match,
            "cardinality_match": bool(cardinality_match),
            "python_filtered": int(python_count),
            "engine_filtered": int(engine_count),
            "delta": int(delta),
            "engine_index_size": engine_index,
            "engine_revision": revision,
            "filters": engine_filters,
            "r18_strict": int(r18_strict),
            "fail_cooldown_before": fail_cooldown_before,
            "pick_probe": pick_probe,
        },
        request_id=rid,
    )

