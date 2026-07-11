from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request

from app.api.admin.deps import get_admin_claims
from app.core.admin_cursor_query import parse_admin_int_cursor, slice_id_cursor_page
from app.core.admin_json import admin_cursor_list
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
    parsed = parse_admin_int_cursor(limit=limit, cursor=cursor, limit_max=200)
    limit = parsed.limit
    cursor_i = parsed.cursor_i

    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    stmt = sa.select(AdminAudit).order_by(AdminAudit.id.desc()).limit(limit + 1)
    if cursor_i is not None:
        stmt = stmt.where(AdminAudit.id < cursor_i)

    async with Session() as session:
        rows = (await session.execute(stmt)).scalars().all()

    items_rows, next_cursor = slice_id_cursor_page(list(rows), limit)

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

    return admin_cursor_list(request, items=items, next_cursor=next_cursor, request_id=rid)

