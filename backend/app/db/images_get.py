from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.images import Image


async def get_image_by_id(session: AsyncSession, *, image_id: int) -> Image | None:
    stmt = select(Image).where(Image.id == int(image_id), Image.status == 1).limit(1)
    return (await session.execute(stmt)).scalars().first()

