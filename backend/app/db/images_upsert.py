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
