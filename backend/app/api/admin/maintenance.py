from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.errors import ApiError, ErrorCode
from app.core.admin_json import admin_ok
from app.core.admin_request import load_json_object_optional, load_json_object, parse_bool, parse_int_in_range
from app.core.random_engine_client import engine_health, random_engine_base_url
from app.core.random_engine_sync import push_engine_snapshot
from app.core.request_id import get_or_create_request_id
from app.db.request_logs_cleanup import (
    DEFAULT_REQUEST_LOGS_CHUNK_SIZE,
    DEFAULT_REQUEST_LOGS_KEEP_DAYS,
    DEFAULT_REQUEST_LOGS_MAX_DELETE_ROWS,
    cleanup_request_logs,
    preview_request_logs_cleanup,
)

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


@router.post("/maintenance/request-logs/cleanup")
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


@router.get("/maintenance/random-engine")
async def random_engine_status(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    settings = getattr(request.app.state, "settings", None)
    base = random_engine_base_url(settings) if settings is not None else None
    enabled = bool(getattr(settings, "random_engine_enabled", False)) if settings is not None else False
    payload: dict[str, Any] = {
        "enabled": enabled,
        "url": base or "",
        "healthy": False,
        "health": None,
    }
    if not base:
        return admin_ok(request, payload=payload, request_id=rid)
    client = getattr(request.app.state, "httpx_client", None)
    if client is None:
        return admin_ok(request, payload=payload, request_id=rid)
    health = await engine_health(client, base, timeout_s=1.0)
    payload["healthy"] = health is not None
    payload["health"] = health
    return admin_ok(request, payload=payload, request_id=rid)


@router.post("/maintenance/random-engine/snapshot")
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
    )
    if result is None:
        raise ApiError(
            code=ErrorCode.UPSTREAM_STREAM_ERROR,
            message="random-engine snapshot failed",
            status_code=502,
        )
    return admin_ok(request, payload={"revision": revision,
        "engine": result}, request_id=rid)

