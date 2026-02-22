from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.image_tags import ImageTag
from app.db.models.tags import Tag


async def get_tag_names_for_image(session: AsyncSession, *, image_id: int) -> list[str]:
    stmt = (
        select(Tag.name)
        .join(ImageTag, ImageTag.tag_id == Tag.id)
        .where(ImageTag.image_id == int(image_id))
        .order_by(Tag.name.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [str(r) for r in rows]

