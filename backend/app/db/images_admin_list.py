from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.image_tags import ImageTag
from app.db.models.images import Image


def _has_any_tag() -> sa.Exists:
    return sa.exists(sa.select(1).where(ImageTag.image_id == Image.id))


async def list_admin_images(
    session: AsyncSession,
    *,
    limit: int,
    cursor: int | None = None,
    missing_keys: Sequence[str] | None = None,
) -> tuple[list[tuple[Image, int]], int | None]:
    """Admin image cursor list (status=1) with optional missing-* filters.

    Returns ((image, tag_count), ...) page and next_cursor image id.

    TAGS-1 pattern: page images first, then COUNT tags only for that page.
    The previous version pre-aggregated the entire image_tags table
    (~421万 links in production) on every page request; the "missing tags"
    filter is NOT EXISTS, which needs no aggregate at all.
    """
    limit_i = int(limit)
    if limit_i < 1:
        raise ValueError("limit must be >= 1")

    stmt = (
        sa.select(Image)
        .where(Image.status == 1)
        .order_by(Image.id.desc())
        .limit(int(limit_i) + 1)
    )
    if cursor is not None:
        stmt = stmt.where(Image.id < int(cursor))

    for key in missing_keys or []:
        if key == "tags":
            stmt = stmt.where(~_has_any_tag())
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

    images = list((await session.execute(stmt)).scalars().all())
    images_page = images[: int(limit_i)]
    next_cursor = int(images_page[-1].id) if len(images) > int(limit_i) and images_page else None

    counts: dict[int, int] = {}
    if images_page:
        page_ids = [int(img.id) for img in images_page]
        count_rows = (
            await session.execute(
                sa.select(ImageTag.image_id, sa.func.count())
                .where(ImageTag.image_id.in_(page_ids))
                .group_by(ImageTag.image_id)
            )
        ).all()
        counts = {int(image_id): int(cnt or 0) for image_id, cnt in count_rows}

    out: list[tuple[Image, int]] = [(img, counts.get(int(img.id), 0)) for img in images_page]
    return out, next_cursor
