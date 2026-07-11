from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.images_get import PUBLIC_IMAGE_LOAD_ONLY
from app.db.models.images import Image
from app.db.pick_filters import build_pick_filter_clauses, clamp_random_key


async def count_pick_candidates(
    session: AsyncSession,
    *,
    r18: int = 0,
    r18_strict: bool = True,
    orientation: int | None = None,
    ai_type: int | None = None,
    illust_type: int | None = None,
    ai_type_allowed: set[int | None] | None = None,
    illust_type_allowed: set[int | None] | None = None,
    min_width: int = 0,
    min_height: int = 0,
    min_pixels: int = 0,
    min_bookmarks: int = 0,
    min_views: int = 0,
    min_comments: int = 0,
    included_tags: Sequence[str] | None = None,
    excluded_tags: Sequence[str] | None = None,
    user_id: int | None = None,
    illust_id: int | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    exclude_image_ids: Sequence[int] | None = None,
    fail_cooldown_before: str | None = None,
) -> int:
    """Count rows matching the same filter clauses as pick_random_* (dual-run ops)."""
    clauses = build_pick_filter_clauses(
        r18=r18,
        r18_strict=r18_strict,
        orientation=orientation,
        ai_type=ai_type,
        illust_type=illust_type,
        ai_type_allowed=ai_type_allowed,
        illust_type_allowed=illust_type_allowed,
        min_width=min_width,
        min_height=min_height,
        min_pixels=min_pixels,
        min_bookmarks=min_bookmarks,
        min_views=min_views,
        min_comments=min_comments,
        included_tags=included_tags,
        excluded_tags=excluded_tags,
        user_id=user_id,
        illust_id=illust_id,
        created_from=created_from,
        created_to=created_to,
        exclude_image_ids=exclude_image_ids,
        fail_cooldown_before=fail_cooldown_before,
    )
    if clauses is None:
        return 0
    stmt = select(func.count()).select_from(Image).where(*clauses)
    return int((await session.execute(stmt)).scalar_one() or 0)


async def pick_random_image(
    session: AsyncSession,
    *,
    r: float,
    r18: int = 0,
    r18_strict: bool = True,
    orientation: int | None = None,
    ai_type: int | None = None,
    illust_type: int | None = None,
    min_width: int = 0,
    min_height: int = 0,
    min_pixels: int = 0,
    min_bookmarks: int = 0,
    min_views: int = 0,
    min_comments: int = 0,
    included_tags: Sequence[str] | None = None,
    excluded_tags: Sequence[str] | None = None,
    user_id: int | None = None,
    illust_id: int | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    exclude_image_ids: Sequence[int] | None = None,
    fail_cooldown_before: str | None = None,
) -> Image | None:
    r = clamp_random_key(r)
    clauses = build_pick_filter_clauses(
        r18=r18,
        r18_strict=r18_strict,
        orientation=orientation,
        ai_type=ai_type,
        illust_type=illust_type,
        min_width=min_width,
        min_height=min_height,
        min_pixels=min_pixels,
        min_bookmarks=min_bookmarks,
        min_views=min_views,
        min_comments=min_comments,
        included_tags=included_tags,
        excluded_tags=excluded_tags,
        user_id=user_id,
        illust_id=illust_id,
        created_from=created_from,
        created_to=created_to,
        exclude_image_ids=exclude_image_ids,
        fail_cooldown_before=fail_cooldown_before,
    )
    if clauses is None:
        return None

    stmt = (
        select(Image)
        .options(PUBLIC_IMAGE_LOAD_ONLY)
        .where(*clauses, Image.random_key >= r)
        .order_by(Image.random_key.asc())
        .limit(1)
    )
    image = (await session.execute(stmt)).scalars().first()
    if image is not None:
        return image

    stmt2 = (
        select(Image)
        .options(PUBLIC_IMAGE_LOAD_ONLY)
        .where(*clauses)
        .order_by(Image.random_key.asc())
        .limit(1)
    )
    return (await session.execute(stmt2)).scalars().first()


async def pick_random_images(
    session: AsyncSession,
    *,
    r: float,
    limit: int,
    r18: int = 0,
    r18_strict: bool = True,
    orientation: int | None = None,
    ai_type: int | None = None,
    illust_type: int | None = None,
    ai_type_allowed: set[int | None] | None = None,
    illust_type_allowed: set[int | None] | None = None,
    min_width: int = 0,
    min_height: int = 0,
    min_pixels: int = 0,
    min_bookmarks: int = 0,
    min_views: int = 0,
    min_comments: int = 0,
    included_tags: Sequence[str] | None = None,
    excluded_tags: Sequence[str] | None = None,
    user_id: int | None = None,
    illust_id: int | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    exclude_image_ids: Sequence[int] | None = None,
    fail_cooldown_before: str | None = None,
) -> list[Image]:
    limit_i = int(limit)
    if limit_i <= 0:
        return []
    if limit_i > 5000:
        limit_i = 5000

    r = clamp_random_key(r)
    clauses = build_pick_filter_clauses(
        r18=r18,
        r18_strict=r18_strict,
        orientation=orientation,
        ai_type=ai_type,
        illust_type=illust_type,
        ai_type_allowed=ai_type_allowed,
        illust_type_allowed=illust_type_allowed,
        min_width=min_width,
        min_height=min_height,
        min_pixels=min_pixels,
        min_bookmarks=min_bookmarks,
        min_views=min_views,
        min_comments=min_comments,
        included_tags=included_tags,
        excluded_tags=excluded_tags,
        user_id=user_id,
        illust_id=illust_id,
        created_from=created_from,
        created_to=created_to,
        exclude_image_ids=exclude_image_ids,
        fail_cooldown_before=fail_cooldown_before,
    )
    if clauses is None:
        return []

    stmt = (
        select(Image)
        .options(PUBLIC_IMAGE_LOAD_ONLY)
        .where(*clauses, Image.random_key >= r)
        .order_by(Image.random_key.asc())
        .limit(limit_i)
    )
    items = (await session.execute(stmt)).scalars().all()
    if len(items) >= limit_i:
        return list(items)

    remain = int(limit_i - len(items))
    if remain <= 0:
        return list(items)

    stmt2 = (
        select(Image)
        .options(PUBLIC_IMAGE_LOAD_ONLY)
        .where(*clauses, Image.random_key < r)
        .order_by(Image.random_key.asc())
        .limit(remain)
    )
    more = (await session.execute(stmt2)).scalars().all()
    if not more:
        return list(items)
    return list(items) + list(more)
