from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.errors import ApiError, ErrorCode
from app.core.request_id import get_or_create_request_id
from app.db.models.admin_audit import AdminAudit
from app.db.session import create_sessionmaker

router = APIRouter()


@router.get("/audit")
async def list_admin_audit(
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

    stmt = sa.select(AdminAudit).order_by(AdminAudit.id.desc()).limit(limit + 1)
    if cursor_i is not None:
        stmt = stmt.where(AdminAudit.id < cursor_i)

    async with Session() as session:
        rows = (await session.execute(stmt)).scalars().all()

    items_rows = rows[:limit]
    next_cursor = int(items_rows[-1].id) if len(rows) > limit and items_rows else None

    def _parse_detail(text: str | None) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return {"raw": raw}
        return data if isinstance(data, dict) else {"value": data}

    items = [
        {
            "id": str(row.id),
            "created_at": row.created_at,
            "actor": row.actor,
            "action": row.action,
            "resource": row.resource,
            "record_id": row.record_id,
            "request_id": row.request_id,
            "detail_json": _parse_detail(row.detail_json),
        }
        for row in items_rows
    ]

    return {
        "ok": True,
        "items": items,
        "next_cursor": str(next_cursor) if next_cursor is not None else "",
        "request_id": rid,
    }

