from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.errors import ApiError, ErrorCode
from app.core.request_id import get_or_create_request_id
from app.db.session import create_sessionmaker
from app.db.tags_list import list_tags as db_list_tags

router = APIRouter()


@router.get("/tags")
async def list_tags(
    request: Request,
    q: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Any:
    if limit < 1 or limit > 100:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported limit", status_code=400)

    q_norm = (q or "").strip()
    if q_norm and len(q_norm) > 200:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported q", status_code=400)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    async with Session() as session:
        items, next_cursor = await db_list_tags(session, limit=limit, cursor=cursor, q=q_norm or None)

    rid = get_or_create_request_id(request)
    resp = JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "items": [
                {
                    "id": str(item.id),
                    "name": item.name,
                    "translated_name": item.translated_name,
                    "count_images": item.count_images,
                }
                for item in items
            ],
            "next_cursor": next_cursor or "",
            "request_id": rid,
        },
    )
    return resp

