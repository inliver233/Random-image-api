from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.random_engine_client import engine_apply_snapshot
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.tags import Tag
from app.db.session import create_sessionmaker

logger = logging.getLogger(__name__)


async def build_engine_snapshot_payload(session: AsyncSession, *, limit: int | None = None) -> dict[str, Any]:
    """Build a full snapshot of enabled images + tag names for the Go engine."""
    stmt = select(Image).where(Image.status == 1).order_by(Image.id.asc())
    if limit is not None and int(limit) > 0:
        stmt = stmt.limit(int(limit))
    images = list((await session.execute(stmt)).scalars().all())
    image_ids = [int(im.id) for im in images]
    tag_names_by_image: dict[int, list[str]] = {i: [] for i in image_ids}
    if image_ids:
        # image_id -> tag names
        rows = (
            await session.execute(
                select(ImageTag.image_id, Tag.name)
                .join(Tag, Tag.id == ImageTag.tag_id)
                .where(ImageTag.image_id.in_(image_ids))
            )
        ).all()
        for image_id, name in rows:
            n = str(name or "").strip()
            if not n:
                continue
            tag_names_by_image.setdefault(int(image_id), []).append(n)

    payload_images: list[dict[str, Any]] = []
    for im in images:
        payload_images.append(
            {
                "id": int(im.id),
                "illust_id": int(im.illust_id),
                "page_index": int(im.page_index),
                "ext": str(im.ext),
                "status": int(im.status),
                "random_key": float(im.random_key),
                "width": im.width,
                "height": im.height,
                "orientation": im.orientation,
                "x_restrict": im.x_restrict,
                "ai_type": im.ai_type,
                "illust_type": im.illust_type,
                "user_id": im.user_id,
                "user_name": im.user_name,
                "title": im.title,
                "created_at_pixiv": im.created_at_pixiv,
                "bookmark_count": im.bookmark_count,
                "view_count": im.view_count,
                "comment_count": im.comment_count,
                "original_url": im.original_url,
                "tag_names": tag_names_by_image.get(int(im.id), []),
                "last_fail_at": im.last_fail_at,
                "last_error_code": im.last_error_code,
            }
        )
    return {"images": payload_images, "count": len(payload_images)}


async def push_engine_snapshot(
    engine: AsyncEngine,
    *,
    base_url: str,
    client: Any,
    revision: str,
    limit: int | None = None,
    timeout_s: float = 60.0,
) -> dict[str, Any] | None:
    Session = create_sessionmaker(engine)
    async with Session() as session:
        built = await build_engine_snapshot_payload(session, limit=limit)
    result = await engine_apply_snapshot(
        client,
        base_url,
        revision=revision,
        images=list(built["images"]),
        timeout_s=timeout_s,
    )
    if result is None:
        logger.warning("random-engine snapshot push failed revision=%s count=%s", revision, built["count"])
    return result
