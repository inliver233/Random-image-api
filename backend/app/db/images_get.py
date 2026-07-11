from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.images import Image


async def get_image_by_id(session: AsyncSession, *, image_id: int) -> Image | None:
    stmt = select(Image).where(Image.id == int(image_id), Image.status == 1).limit(1)
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
        (await session.execute(select(Image).where(Image.id.in_(ids), Image.status == 1))).scalars().all()
    )
    by_id = {int(im.id): im for im in rows}
    return [by_id[i] for i in ids if i in by_id]

