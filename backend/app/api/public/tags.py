from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.public_json import public_cursor_list_json
from app.core.public_search_query import parse_public_search_query
from app.db.session import resolve_sessionmaker
from app.db.tag_store import resolve_tag_store

router = APIRouter()


@router.get(
    "/tags",
    summary="List catalog tags",
    description=(
        "Cursor-paginated tag list (`limit`, `cursor`, optional `q` name filter). "
        "TAGS-1: pages tags first then counts only the page (avoids full-catalog "
        "join+GROUP BY). When `PUBLIC_API_KEY_REQUIRED`, send `X-API-Key` or `?api_key=`."
    ),
)
async def list_tags(
    request: Request,
    q: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Any:
    parsed = parse_public_search_query(q=q, limit=limit, cursor=cursor, cursor_kind="str")

    engine = request.app.state.engine
    Session = resolve_sessionmaker(request, engine)
    tags = resolve_tag_store(getattr(request.app.state, "tag_store", None))
    async with Session() as session:
        items, next_cursor = await tags.list_tags(
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
