from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.public_json import public_cursor_list_json
from app.core.public_search_query import parse_public_search_query
from app.core.random_delivery import resolve_catalog_store
from app.db.session import resolve_sessionmaker
router = APIRouter()


@router.get("/authors")
async def list_authors(
    request: Request,
    q: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Any:
    parsed = parse_public_search_query(q=q, limit=limit, cursor=cursor, cursor_kind="int")

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)
    catalog = resolve_catalog_store(getattr(request.app.state, "catalog_store", None))
    async with Session() as session:
        items, next_cursor = await catalog.list_authors(
            session,
            limit=parsed.limit,
            cursor=parsed.cursor_i,
            q=parsed.q,
        )

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
