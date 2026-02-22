from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.errors import ApiError, ErrorCode
from app.core.request_id import get_or_create_request_id
from app.db.request_logs_cleanup import (
    DEFAULT_REQUEST_LOGS_CHUNK_SIZE,
    DEFAULT_REQUEST_LOGS_KEEP_DAYS,
    DEFAULT_REQUEST_LOGS_MAX_DELETE_ROWS,
    cleanup_request_logs,
    preview_request_logs_cleanup,
)

router = APIRouter()


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    return default


async def _load_cleanup_request_logs_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc
    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)

    keep_days_raw = data.get("keep_days", DEFAULT_REQUEST_LOGS_KEEP_DAYS)
    try:
        keep_days = int(keep_days_raw)
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported keep_days", status_code=400) from exc
    if keep_days < 0 or keep_days > 36500:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported keep_days", status_code=400)

    max_delete_rows_raw = data.get("max_delete_rows", DEFAULT_REQUEST_LOGS_MAX_DELETE_ROWS)
    try:
        max_delete_rows = int(max_delete_rows_raw)
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported max_delete_rows", status_code=400) from exc
    if max_delete_rows < 1 or max_delete_rows > 10_000_000:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported max_delete_rows", status_code=400)

    chunk_size_raw = data.get("chunk_size", DEFAULT_REQUEST_LOGS_CHUNK_SIZE)
    try:
        chunk_size = int(chunk_size_raw)
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported chunk_size", status_code=400) from exc
    if chunk_size < 1 or chunk_size > 100_000:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported chunk_size", status_code=400)

    dry_run = _parse_bool(data.get("dry_run"), default=False)

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
        return {
            "ok": True,
            "dry_run": True,
            "cutoff": preview.cutoff,
            "would_delete": int(preview.would_delete),
            "has_more": bool(preview.has_more),
            "request_id": rid,
        }

    result = await cleanup_request_logs(
        engine,
        keep_days=int(cfg["keep_days"]),
        max_delete_rows=int(cfg["max_delete_rows"]),
        chunk_size=int(cfg["chunk_size"]),
    )
    return {
        "ok": True,
        "dry_run": False,
        "cutoff": result.cutoff,
        "deleted": int(result.deleted),
        "has_more": bool(result.has_more),
        "request_id": rid,
    }

