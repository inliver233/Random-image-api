from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.images import Image


async def get_image_by_illust_page(
    session: AsyncSession,
    *,
    illust_id: int,
    page_index: int,
) -> Image | None:
    stmt = (
        select(Image)
        .where(
            Image.status == 1,
            Image.illust_id == int(illust_id),
            Image.page_index == int(page_index),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()

