from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError

from app.api.admin.deps import get_admin_claims
from app.core.api_keys import api_key_hint, hmac_sha256_hex
from app.core.errors import ApiError, ErrorCode
from app.core.request_id import get_or_create_request_id
from app.core.time import iso_utc_ms
from app.db.models.api_keys import ApiKey
from app.db.session import create_sessionmaker, with_sqlite_busy_retry

router = APIRouter()


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "y", "on"}:
            return True
        if v in {"false", "0", "no", "n", "off"}:
            return False
    return None


async def _load_json_object(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc
    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)
    return data


@router.get("/api-keys")
async def list_api_keys(
    request: Request,
    limit: int = 50,
    cursor: str | None = None,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if limit < 1 or limit > 200:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported limit", status_code=400)

    cursor_i: int | None = None
    cursor_raw = (cursor or "").strip()
    if cursor_raw:
        if not cursor_raw.isdigit():
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported cursor", status_code=400)
        cursor_i = int(cursor_raw)
        if cursor_i <= 0:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported cursor", status_code=400)

    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    stmt = sa.select(ApiKey).order_by(ApiKey.id.desc()).limit(limit + 1)
    if cursor_i is not None:
        stmt = stmt.where(ApiKey.id < cursor_i)

    async with Session() as session:
        rows = ((await session.execute(stmt)).scalars().all())

    items_rows = rows[:limit]
    next_cursor = int(items_rows[-1].id) if len(rows) > limit and items_rows else None

    items = [
        {
            "id": str(row.id),
            "name": row.name,
            "description": row.description,
            "enabled": bool(row.enabled),
            "hint": row.hint,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "last_used_at": row.last_used_at,
        }
        for row in items_rows
    ]

    return {
        "ok": True,
        "items": items,
        "next_cursor": str(next_cursor) if next_cursor is not None else "",
        "request_id": rid,
    }


@router.post("/api-keys")
async def create_api_key(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)

    body = await _load_json_object(request)
    name = str(body.get("name") or "").strip()
    api_key = str(body.get("api_key") or "").strip()
    description = str(body.get("description") or "").strip() or None
    enabled_v = _as_bool(body.get("enabled"))
    enabled = bool(enabled_v) if enabled_v is not None else True

    if not name or len(name) > 100:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid name", status_code=400)
    if not api_key or len(api_key) < 20 or len(api_key) > 500:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid api_key", status_code=400)
    if description is not None and len(description) > 1000:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid description", status_code=400)

    settings = request.app.state.settings
    try:
        key_hash = hmac_sha256_hex(secret_key=settings.secret_key, message=api_key)
    except Exception as exc:
        raise ApiError(code=ErrorCode.INTERNAL_ERROR, message="API key hashing not configured", status_code=500) from exc

    hint = api_key_hint(api_key)
    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async def _op() -> int:
        async with Session() as session:
            row = ApiKey(
                name=name,
                description=description,
                key_hash=key_hash,
                hint=hint,
                enabled=1 if enabled else 0,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="API key name exists", status_code=400) from exc
            await session.refresh(row)
            return int(row.id)

    api_key_id = await with_sqlite_busy_retry(_op)
    return {"ok": True, "api_key_id": str(api_key_id), "hint": hint, "request_id": rid}


@router.put("/api-keys/{api_key_id}")
async def update_api_key(
    api_key_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if api_key_id <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid api_key_id", status_code=400)

    rid = get_or_create_request_id(request)
    body = await _load_json_object(request)

    enabled_v = _as_bool(body.get("enabled")) if "enabled" in body else None
    description_raw = body.get("description") if "description" in body else None
    description = str(description_raw).strip() if isinstance(description_raw, str) else None
    if description is not None and not description:
        description = None
    if description is not None and len(description) > 1000:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid description", status_code=400)

    if enabled_v is None and "description" not in body:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing fields", status_code=400)

    now = iso_utc_ms()

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            row = await session.get(ApiKey, int(api_key_id))
            if row is None:
                raise ApiError(code=ErrorCode.NOT_FOUND, message="API key not found", status_code=404)
            if enabled_v is not None:
                row.enabled = 1 if bool(enabled_v) else 0
            if "description" in body:
                row.description = description
            row.updated_at = now
            await session.commit()

        return {"ok": True, "api_key_id": str(api_key_id), "request_id": rid}

    return await with_sqlite_busy_retry(_op)

