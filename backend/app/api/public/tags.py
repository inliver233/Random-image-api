from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.public_json import public_cursor_list_json
from app.core.public_search_query import parse_public_search_query
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
    parsed = parse_public_search_query(q=q, limit=limit, cursor=cursor, cursor_kind="str")

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    async with Session() as session:
        items, next_cursor = await db_list_tags(
            session,
            limit=parsed.limit,
            cursor=parsed.cursor_s,
            q=parsed.q,
        )

    return public_cursor_list_json(
        request,
        items=[
            {
                "id": str(item.id),
                "name": item.name,
                "translated_name": item.translated_name,
                "count_images": item.count_images,
            }
            for item in items
        ],
        next_cursor=next_cursor,
    )
