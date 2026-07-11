from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.db.models.images import Image

# Shared with random_pick: public delivery + score + hydrate columns only.
_PUBLIC_IMAGE_LOAD_ONLY = load_only(
    Image.id,
    Image.illust_id,
    Image.page_index,
    Image.ext,
    Image.original_url,
    Image.random_key,
    Image.width,
    Image.height,
    Image.orientation,
    Image.x_restrict,
    Image.ai_type,
    Image.illust_type,
    Image.user_id,
    Image.user_name,
    Image.title,
    Image.created_at_pixiv,
    Image.bookmark_count,
    Image.view_count,
    Image.comment_count,
    Image.status,
    Image.last_ok_at,
    Image.last_error_code,
    # quality time-boost may fall back to added_at when created_at_pixiv is null
    Image.added_at,
)


async def get_image_by_id(session: AsyncSession, *, image_id: int) -> Image | None:
    stmt = (
        select(Image)
        .options(_PUBLIC_IMAGE_LOAD_ONLY)
        .where(Image.id == int(image_id), Image.status == 1)
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def get_images_by_ids(session: AsyncSession, *, image_ids: list[int]) -> list[Image]:
    """Load enabled images; return in the same order as ``image_ids`` (skip missing)."""
    ids: list[int] = []
    seen: set[int] = set()
    for raw in image_ids:
        try:
            i = int(raw)
        except Exception:
            continue
        if i <= 0 or i in seen:
            continue
        seen.add(i)
        ids.append(i)
    if not ids:
        return []
    rows = list(
        (
            await session.execute(
                select(Image).options(_PUBLIC_IMAGE_LOAD_ONLY).where(Image.id.in_(ids), Image.status == 1)
            )
        )
        .scalars()
        .all()
    )
    by_id = {int(im.id): im for im in rows}
    return [by_id[i] for i in ids if i in by_id]

