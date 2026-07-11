from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.image_tags import ImageTag
from app.db.models.images import Image


async def list_admin_images(
    session: AsyncSession,
    *,
    limit: int,
    cursor: int | None = None,
    missing_keys: Sequence[str] | None = None,
) -> tuple[list[tuple[Image, int]], int | None]:
    """Admin image cursor list (status=1) with optional missing-* filters.

    Returns ((image, tag_count), ...) page and next_cursor image id.
    """
    limit_i = int(limit)
    if limit_i < 1:
        raise ValueError("limit must be >= 1")

    tag_counts = (
        sa.select(ImageTag.image_id.label("image_id"), sa.func.count().label("tag_count"))
        .group_by(ImageTag.image_id)
        .subquery()
    )
    tag_count_col = sa.func.coalesce(tag_counts.c.tag_count, 0).label("tag_count")

    stmt = (
        sa.select(Image, tag_count_col)
        .outerjoin(tag_counts, tag_counts.c.image_id == Image.id)
        .where(Image.status == 1)
        .order_by(Image.id.desc())
        .limit(int(limit_i) + 1)
    )
    if cursor is not None:
        stmt = stmt.where(Image.id < int(cursor))

    for key in missing_keys or []:
        if key == "tags":
            stmt = stmt.where(tag_count_col == 0)
        elif key == "geometry":
            stmt = stmt.where((Image.width.is_(None)) | (Image.height.is_(None)))
        elif key == "r18":
            stmt = stmt.where(Image.x_restrict.is_(None))
        elif key == "ai":
            stmt = stmt.where(Image.ai_type.is_(None))
        elif key == "illust_type":
            stmt = stmt.where(Image.illust_type.is_(None))
        elif key == "user":
            stmt = stmt.where(Image.user_id.is_(None))
        elif key == "title":
            stmt = stmt.where((Image.title.is_(None)) | (sa.func.trim(Image.title) == ""))
        elif key == "created_at":
            stmt = stmt.where((Image.created_at_pixiv.is_(None)) | (sa.func.trim(Image.created_at_pixiv) == ""))
        elif key == "popularity":
            stmt = stmt.where(
                (Image.bookmark_count.is_(None)) | (Image.view_count.is_(None)) | (Image.comment_count.is_(None))
            )

    rows = (await session.execute(stmt)).all()
    rows_page = rows[: int(limit_i)]
    next_cursor = int(rows_page[-1][0].id) if len(rows) > int(limit_i) and rows_page else None
    out: list[tuple[Image, int]] = [(img, int(tag_count or 0)) for img, tag_count in rows_page]
    return out, next_cursor
