from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.tags import Tag


def _r18_where_clause(*, r18: int, r18_strict: bool) -> object | None:
    r18_i = int(r18)
    if r18_i not in {0, 1, 2}:
        raise ValueError("r18 must be 0, 1, or 2")

    if r18_i == 2:
        return None

    if r18_i == 1:
        return Image.x_restrict == 1

    if bool(r18_strict):
        return Image.x_restrict == 0

    return (Image.x_restrict == 0) | (Image.x_restrict.is_(None))


def _orientation_where_clause(*, orientation: int | None) -> object | None:
    if orientation is None:
        return None
    orientation_i = int(orientation)
    if orientation_i not in {1, 2, 3}:
        raise ValueError("orientation must be 1, 2, or 3")
    return Image.orientation == orientation_i


def _clean_tag_names(values: Sequence[str] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        name = str(v or "").strip()
        if not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _included_tags_where_clause(*, tag_names: Sequence[str]) -> object | None:
    names = _clean_tag_names(tag_names)
    if not names:
        return None
    subq = (
        select(ImageTag.image_id)
        .join(Tag, Tag.id == ImageTag.tag_id)
        .where(Tag.name.in_(names))
        .group_by(ImageTag.image_id)
        .having(func.count(distinct(Tag.name)) == len(names))
    )
    return Image.id.in_(subq)


def _excluded_tags_where_clause(*, tag_names: Sequence[str]) -> object | None:
    names = _clean_tag_names(tag_names)
    if not names:
        return None
    subq = (
        select(ImageTag.image_id)
        .join(Tag, Tag.id == ImageTag.tag_id)
        .where(Tag.name.in_(names))
    )
    return Image.id.not_in(subq)


def _exclude_image_ids_where_clause(*, image_ids: Sequence[int] | None) -> object | None:
    if not image_ids:
        return None
    ids: list[int] = []
    seen: set[int] = set()
    for raw in image_ids:
        try:
            value = int(raw)
        except Exception:
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        ids.append(value)
    if not ids:
        return None
    return Image.id.not_in(ids)


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
    r = float(r)
    if r < 0.0:
        r = 0.0
    if r >= 1.0:
        r = 0.999999999

    r18_clause = _r18_where_clause(r18=r18, r18_strict=r18_strict)
    orientation_clause = _orientation_where_clause(orientation=orientation)
    included_tags_clause = _included_tags_where_clause(tag_names=included_tags or [])
    excluded_tags_clause = _excluded_tags_where_clause(tag_names=excluded_tags or [])
    exclude_ids_clause = _exclude_image_ids_where_clause(image_ids=exclude_image_ids)

    min_width_i = int(min_width)
    min_height_i = int(min_height)
    min_pixels_i = int(min_pixels)
    min_bookmarks_i = int(min_bookmarks)
    min_views_i = int(min_views)
    min_comments_i = int(min_comments)
    if min_width_i < 0 or min_height_i < 0 or min_pixels_i < 0:
        raise ValueError("min_* must be >= 0")
    if min_bookmarks_i < 0 or min_views_i < 0 or min_comments_i < 0:
        raise ValueError("min_* must be >= 0")

    clauses = [Image.status == 1]
    if r18_clause is not None:
        clauses.append(r18_clause)
    if orientation_clause is not None:
        clauses.append(orientation_clause)
    if ai_type is not None:
        ai_type_i = int(ai_type)
        if ai_type_i not in {0, 1}:
            raise ValueError("ai_type must be 0, 1, or None")
        clauses.append(Image.ai_type == ai_type_i)
    if illust_type is not None:
        illust_type_i = int(illust_type)
        if illust_type_i not in {0, 1, 2}:
            raise ValueError("illust_type must be 0, 1, 2, or None")
        clauses.append(Image.illust_type == illust_type_i)
    if included_tags_clause is not None:
        clauses.append(included_tags_clause)
    if excluded_tags_clause is not None:
        clauses.append(excluded_tags_clause)
    if exclude_ids_clause is not None:
        clauses.append(exclude_ids_clause)
    if min_width_i > 0:
        clauses.append(Image.width >= min_width_i)
    if min_height_i > 0:
        clauses.append(Image.height >= min_height_i)
    if min_pixels_i > 0:
        clauses.append((Image.width * Image.height) >= min_pixels_i)
    if min_bookmarks_i > 0:
        clauses.append(Image.bookmark_count >= min_bookmarks_i)
    if min_views_i > 0:
        clauses.append(Image.view_count >= min_views_i)
    if min_comments_i > 0:
        clauses.append(Image.comment_count >= min_comments_i)
    if user_id is not None:
        clauses.append(Image.user_id == int(user_id))
    if illust_id is not None:
        clauses.append(Image.illust_id == int(illust_id))
    if created_from is not None:
        clauses.append(Image.created_at_pixiv >= str(created_from))
    if created_to is not None:
        clauses.append(Image.created_at_pixiv <= str(created_to))
    if fail_cooldown_before is not None:
        clauses.append((Image.last_fail_at.is_(None)) | (Image.last_fail_at <= str(fail_cooldown_before)))

    stmt = (
        select(Image)
        .where(*clauses, Image.random_key >= r)
        .order_by(Image.random_key.asc())
        .limit(1)
    )
    image = (await session.execute(stmt)).scalars().first()
    if image is not None:
        return image

    stmt2 = (
        select(Image)
        .where(*clauses)
        .order_by(Image.random_key.asc())
        .limit(1)
    )
    return (await session.execute(stmt2)).scalars().first()


def _allowed_int_or_null_clause(column: object, *, allowed: set[int | None]) -> object | None:
    allowed_norm: set[int | None] = set(allowed)
    ints = sorted({int(v) for v in allowed_norm if v is not None})
    has_null = None in allowed_norm

    if ints and has_null and set(ints) in ({0, 1}, {0, 1, 2}):
        return None

    if ints and has_null:
        return or_(column.in_(ints), column.is_(None))  # type: ignore[attr-defined]
    if ints:
        if len(ints) == 1:
            return column == ints[0]  # type: ignore[operator]
        return column.in_(ints)  # type: ignore[attr-defined]
    if has_null:
        return column.is_(None)  # type: ignore[attr-defined]
    return None


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

    r = float(r)
    if r < 0.0:
        r = 0.0
    if r >= 1.0:
        r = 0.999999999

    r18_clause = _r18_where_clause(r18=r18, r18_strict=r18_strict)
    orientation_clause = _orientation_where_clause(orientation=orientation)
    included_tags_clause = _included_tags_where_clause(tag_names=included_tags or [])
    excluded_tags_clause = _excluded_tags_where_clause(tag_names=excluded_tags or [])
    exclude_ids_clause = _exclude_image_ids_where_clause(image_ids=exclude_image_ids)

    min_width_i = int(min_width)
    min_height_i = int(min_height)
    min_pixels_i = int(min_pixels)
    min_bookmarks_i = int(min_bookmarks)
    min_views_i = int(min_views)
    min_comments_i = int(min_comments)
    if min_width_i < 0 or min_height_i < 0 or min_pixels_i < 0:
        raise ValueError("min_* must be >= 0")
    if min_bookmarks_i < 0 or min_views_i < 0 or min_comments_i < 0:
        raise ValueError("min_* must be >= 0")

    clauses = [Image.status == 1]
    if r18_clause is not None:
        clauses.append(r18_clause)
    if orientation_clause is not None:
        clauses.append(orientation_clause)
    if ai_type is not None:
        ai_type_i = int(ai_type)
        if ai_type_i not in {0, 1}:
            raise ValueError("ai_type must be 0, 1, or None")
        clauses.append(Image.ai_type == ai_type_i)
    if illust_type is not None:
        illust_type_i = int(illust_type)
        if illust_type_i not in {0, 1, 2}:
            raise ValueError("illust_type must be 0, 1, 2, or None")
        clauses.append(Image.illust_type == illust_type_i)
    if ai_type_allowed is not None:
        allowed = set(ai_type_allowed)
        if not allowed:
            return []
        clause = _allowed_int_or_null_clause(Image.ai_type, allowed=allowed)
        if clause is not None:
            clauses.append(clause)
    if illust_type_allowed is not None:
        allowed = set(illust_type_allowed)
        if not allowed:
            return []
        clause = _allowed_int_or_null_clause(Image.illust_type, allowed=allowed)
        if clause is not None:
            clauses.append(clause)
    if included_tags_clause is not None:
        clauses.append(included_tags_clause)
    if excluded_tags_clause is not None:
        clauses.append(excluded_tags_clause)
    if exclude_ids_clause is not None:
        clauses.append(exclude_ids_clause)
    if min_width_i > 0:
        clauses.append(Image.width >= min_width_i)
    if min_height_i > 0:
        clauses.append(Image.height >= min_height_i)
    if min_pixels_i > 0:
        clauses.append((Image.width * Image.height) >= min_pixels_i)
    if min_bookmarks_i > 0:
        clauses.append(Image.bookmark_count >= min_bookmarks_i)
    if min_views_i > 0:
        clauses.append(Image.view_count >= min_views_i)
    if min_comments_i > 0:
        clauses.append(Image.comment_count >= min_comments_i)
    if user_id is not None:
        clauses.append(Image.user_id == int(user_id))
    if illust_id is not None:
        clauses.append(Image.illust_id == int(illust_id))
    if created_from is not None:
        clauses.append(Image.created_at_pixiv >= str(created_from))
    if created_to is not None:
        clauses.append(Image.created_at_pixiv <= str(created_to))
    if fail_cooldown_before is not None:
        clauses.append((Image.last_fail_at.is_(None)) | (Image.last_fail_at <= str(fail_cooldown_before)))

    stmt = (
        select(Image)
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
        .where(*clauses, Image.random_key < r)
        .order_by(Image.random_key.asc())
        .limit(remain)
    )
    more = (await session.execute(stmt2)).scalars().all()
    if not more:
        return list(items)
    return list(items) + list(more)
