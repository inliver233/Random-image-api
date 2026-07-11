from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.errors import ApiError, ErrorCode
from app.core.public_json import public_cursor_list_json
from app.db.authors_list import list_authors as db_list_authors
from app.db.session import create_sessionmaker

router = APIRouter()


@router.get("/authors")
async def list_authors(
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

    cursor_i: int | None = None
    cursor_raw = (cursor or "").strip()
    if cursor_raw:
        if not cursor_raw.isdigit():
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported cursor", status_code=400)
        cursor_i = int(cursor_raw)
        if cursor_i <= 0:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported cursor", status_code=400)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    async with Session() as session:
        items, next_cursor = await db_list_authors(session, limit=limit, cursor=cursor_i, q=q_norm or None)

    return public_cursor_list_json(
        request,
        items=[
            {
                "user_id": str(item.user_id),
                "user_name": item.user_name,
                "count_images": item.count_images,
            }
            for item in items
        ],
        next_cursor=next_cursor,
    )
