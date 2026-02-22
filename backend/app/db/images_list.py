from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.images import Image
from app.db.random_pick import (
    _excluded_tags_where_clause,
    _included_tags_where_clause,
    _orientation_where_clause,
    _r18_where_clause,
)


async def list_images(
    session: AsyncSession,
    *,
    limit: int,
    cursor: int | None = None,
    r18: int = 0,
    r18_strict: bool = True,
    orientation: int | None = None,
    ai_type: int | None = None,
    min_width: int = 0,
    min_height: int = 0,
    min_pixels: int = 0,
    included_tags: Sequence[str] | None = None,
    excluded_tags: Sequence[str] | None = None,
    user_id: int | None = None,
    illust_id: int | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> tuple[list[Image], int | None]:
    limit_i = int(limit)
    if limit_i < 1:
        raise ValueError("limit must be >= 1")

    r18_clause = _r18_where_clause(r18=r18, r18_strict=r18_strict)
    orientation_clause = _orientation_where_clause(orientation=orientation)
    included_tags_clause = _included_tags_where_clause(tag_names=included_tags or [])
    excluded_tags_clause = _excluded_tags_where_clause(tag_names=excluded_tags or [])

    min_width_i = int(min_width)
    min_height_i = int(min_height)
    min_pixels_i = int(min_pixels)
    if min_width_i < 0 or min_height_i < 0 or min_pixels_i < 0:
        raise ValueError("min_* must be >= 0")

    clauses: list[object] = [Image.status == 1]
    if cursor is not None:
        clauses.append(Image.id < int(cursor))
    if r18_clause is not None:
        clauses.append(r18_clause)
    if orientation_clause is not None:
        clauses.append(orientation_clause)
    if ai_type is not None:
        ai_type_i = int(ai_type)
        if ai_type_i not in {0, 1}:
            raise ValueError("ai_type must be 0, 1, or None")
        clauses.append(Image.ai_type == ai_type_i)
    if included_tags_clause is not None:
        clauses.append(included_tags_clause)
    if excluded_tags_clause is not None:
        clauses.append(excluded_tags_clause)
    if min_width_i > 0:
        clauses.append(Image.width >= min_width_i)
    if min_height_i > 0:
        clauses.append(Image.height >= min_height_i)
    if min_pixels_i > 0:
        clauses.append((Image.width * Image.height) >= min_pixels_i)
    if user_id is not None:
        clauses.append(Image.user_id == int(user_id))
    if illust_id is not None:
        clauses.append(Image.illust_id == int(illust_id))
    if created_from is not None:
        clauses.append(Image.created_at_pixiv >= str(created_from))
    if created_to is not None:
        clauses.append(Image.created_at_pixiv <= str(created_to))

    stmt = select(Image).where(*clauses).order_by(Image.id.desc()).limit(limit_i + 1)
    images = (await session.execute(stmt)).scalars().all()

    next_cursor: int | None = None
    if len(images) > limit_i:
        images = images[:limit_i]
        next_cursor = int(images[-1].id)

    return images, next_cursor

