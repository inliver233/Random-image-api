from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.images import Image


async def delete_images_by_ids(
    session: AsyncSession,
    *,
    image_ids: list[int],
) -> list[int]:
    """Delete Image rows by id. Caller owns tags / commit.

    Returns ids that existed before delete (for engine publish).
    """
    ids = [int(x) for x in image_ids if int(x) > 0]
    if not ids:
        return []

    found: list[int] = []
    # SQLite variable limit: chunk large id lists.
    chunk_size = 900
    for offset in range(0, len(ids), chunk_size):
        chunk = ids[offset : offset + chunk_size]
        rows = list((await session.execute(select(Image.id).where(Image.id.in_(chunk)))).scalars().all())
        found.extend(int(x) for x in rows)
        if rows:
            await session.execute(delete(Image).where(Image.id.in_(chunk)))
    return found
