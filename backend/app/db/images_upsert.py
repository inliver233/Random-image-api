from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.images import Image


async def upsert_image_by_illust_page(
    session: AsyncSession,
    *,
    illust_id: int,
    page_index: int,
    ext: str,
    original_url: str,
    proxy_path: str,
    random_key: float,
    created_import_id: int | None,
) -> int:
    stmt = sqlite_insert(Image).values(
        illust_id=illust_id,
        page_index=page_index,
        ext=ext,
        original_url=original_url,
        proxy_path=proxy_path,
        random_key=random_key,
        created_import_id=created_import_id,
    )

    now_expr = sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
    stmt = stmt.on_conflict_do_update(
        index_elements=["illust_id", "page_index"],
        set_={
            "ext": stmt.excluded.ext,
            "original_url": stmt.excluded.original_url,
            "proxy_path": stmt.excluded.proxy_path,
            "created_import_id": stmt.excluded.created_import_id,
            "updated_at": now_expr,
        },
    ).returning(Image.id)

    result = await session.execute(stmt)
    return int(result.scalar_one())


async def upsert_hydrated_image_page(
    session: AsyncSession,
    *,
    illust_id: int,
    page_index: int,
    ext: str,
    original_url: str,
    random_key: float,
    width: int | None,
    height: int | None,
    aspect_ratio: float | None,
    orientation: int | None,
    x_restrict: int | None,
    ai_type: int | None,
    illust_type: int | None,
    user_id: int | None,
    user_name: str | None,
    title: str | None,
    created_at_pixiv: str | None,
    bookmark_count: int | None,
    view_count: int | None,
    comment_count: int | None,
    created_import_id: int | None,
) -> int:
    """Upsert one page with hydrate metadata; set proxy_path from returned id.

    Does not touch tags (ImageTag) — callers replace tags separately.
    On conflict, preserves existing random_key / status / fail counters.
    """
    now_expr = sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
    stmt = sqlite_insert(Image).values(
        illust_id=int(illust_id),
        page_index=int(page_index),
        ext=str(ext),
        original_url=str(original_url),
        proxy_path="",
        random_key=float(random_key),
        width=width,
        height=height,
        aspect_ratio=aspect_ratio,
        orientation=orientation,
        x_restrict=x_restrict,
        ai_type=ai_type,
        illust_type=illust_type,
        user_id=user_id,
        user_name=user_name,
        title=title,
        created_at_pixiv=created_at_pixiv,
        bookmark_count=bookmark_count,
        view_count=view_count,
        comment_count=comment_count,
        created_import_id=int(created_import_id) if created_import_id else None,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["illust_id", "page_index"],
        set_={
            "ext": stmt.excluded.ext,
            "original_url": stmt.excluded.original_url,
            "width": stmt.excluded.width,
            "height": stmt.excluded.height,
            "aspect_ratio": stmt.excluded.aspect_ratio,
            "orientation": stmt.excluded.orientation,
            "x_restrict": stmt.excluded.x_restrict,
            "ai_type": stmt.excluded.ai_type,
            "illust_type": stmt.excluded.illust_type,
            "user_id": stmt.excluded.user_id,
            "user_name": stmt.excluded.user_name,
            "title": stmt.excluded.title,
            "created_at_pixiv": stmt.excluded.created_at_pixiv,
            "bookmark_count": stmt.excluded.bookmark_count,
            "view_count": stmt.excluded.view_count,
            "comment_count": stmt.excluded.comment_count,
            "updated_at": now_expr,
        },
    ).returning(Image.id)

    result = await session.execute(stmt)
    image_id = int(result.scalar_one())
    proxy_path = f"/i/{image_id}.{ext}"
    await session.execute(sa.update(Image).where(Image.id == image_id).values(proxy_path=proxy_path))
    return image_id
