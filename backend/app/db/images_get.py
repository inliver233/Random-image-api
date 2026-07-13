from __future__ import annotations

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.db.models.images import Image

# Public pick/rehydrate path: delivery + score + hydrate signal columns only
# (skip admin/error blobs). Shared by random_pick and engine id rehydrate.
PUBLIC_IMAGE_LOAD_ONLY = load_only(
    Image.id,
    Image.illust_id,
    Image.page_index,
    Image.ext,
    Image.original_url,
    Image.random_key,
    Image.width,
    Image.height,
    Image.orientation,
    Image.x_restrict,
    Image.ai_type,
    Image.illust_type,
    Image.user_id,
    Image.user_name,
    Image.title,
    Image.created_at_pixiv,
    Image.bookmark_count,
    Image.view_count,
    Image.comment_count,
    Image.status,
    Image.last_ok_at,
    Image.last_error_code,
    # quality time-boost may fall back to added_at when created_at_pixiv is null
    Image.added_at,
)


async def get_image_by_id(session: AsyncSession, *, image_id: int) -> Image | None:
    stmt = (
        select(Image)
        .options(PUBLIC_IMAGE_LOAD_ONLY)
        .where(Image.id == int(image_id), Image.status == 1)
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def get_image_by_id_any_status(session: AsyncSession, *, image_id: int) -> Image | None:
    """Load one image by primary key regardless of status (admin/manual hydrate resolve)."""
    if int(image_id) <= 0:
        return None
    return (await session.execute(select(Image).where(Image.id == int(image_id)).limit(1))).scalars().first()


def _normalize_positive_ids(image_ids: list[int]) -> list[int]:
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
    return ids


# SQLite default bind limit ~999; keep headroom like tags_links / images_delete.
_IMAGE_ID_IN_CHUNK = 900


async def get_images_by_ids(session: AsyncSession, *, image_ids: list[int]) -> list[Image]:
    """Load enabled images; return in the same order as ``image_ids`` (skip missing)."""
    ids = _normalize_positive_ids(image_ids)
    if not ids:
        return []
    by_id: dict[int, Image] = {}
    for offset in range(0, len(ids), _IMAGE_ID_IN_CHUNK):
        chunk = ids[offset : offset + _IMAGE_ID_IN_CHUNK]
        rows = list(
            (
                await session.execute(
                    select(Image)
                    .options(PUBLIC_IMAGE_LOAD_ONLY)
                    .where(Image.id.in_(chunk), Image.status == 1)
                )
            )
            .scalars()
            .all()
        )
        for im in rows:
            by_id[int(im.id)] = im
    return [by_id[i] for i in ids if i in by_id]


async def get_images_by_ids_any_status(session: AsyncSession, *, image_ids: list[int]) -> list[Image]:
    """Load images of any status; return in the same order as ``image_ids`` (skip missing).

    Used by Random Engine event publish (status changes must reach the index).
    Full row load — engine payload needs fail/error columns not in PUBLIC_IMAGE_LOAD_ONLY.
    Chunked IN for SQLite bind limits (ENGINE-1 parity with tag map).
    """
    ids = _normalize_positive_ids(image_ids)
    if not ids:
        return []
    by_id: dict[int, Image] = {}
    for offset in range(0, len(ids), _IMAGE_ID_IN_CHUNK):
        chunk = ids[offset : offset + _IMAGE_ID_IN_CHUNK]
        rows = list((await session.execute(select(Image).where(Image.id.in_(chunk)))).scalars().all())
        for im in rows:
            by_id[int(im.id)] = im
    return [by_id[i] for i in ids if i in by_id]


async def get_images_by_illust_id(session: AsyncSession, *, illust_id: int) -> list[Image]:
    """Load all pages for an illust (any status), ordered by page_index ascending."""
    if int(illust_id) <= 0:
        return []
    return list(
        (
            await session.execute(
                select(Image).where(Image.illust_id == int(illust_id)).order_by(Image.page_index.asc())
            )
        )
        .scalars()
        .all()
    )


async def list_enabled_images(
    session: AsyncSession,
    *,
    limit: int | None = None,
    after_id: int | None = None,
) -> list[Image]:
    """Load status=1 images ordered by id ascending (engine full snapshot).

    ENGINE-1: optional ``after_id`` keyset cursor for large-catalog paging
    (id > after_id). Callers stream pages instead of materializing 62万 rows at once.
    """
    stmt = select(Image).where(Image.status == 1)
    if after_id is not None and int(after_id) > 0:
        stmt = stmt.where(Image.id > int(after_id))
    stmt = stmt.order_by(Image.id.asc())
    if limit is not None and int(limit) > 0:
        stmt = stmt.limit(int(limit))
    return list((await session.execute(stmt)).scalars().all())


async def count_enabled_images(session: AsyncSession) -> int:
    """Return the authoritative number of status=1 rows for snapshot validation."""
    stmt = select(func.count()).select_from(Image).where(Image.status == 1)
    return int((await session.execute(stmt)).scalar_one() or 0)


async def map_image_ids_by_illust_page(
    session: AsyncSession,
    *,
    keys: list[tuple[int, int]],
) -> dict[tuple[int, int], int]:
    """Map (illust_id, page_index) → image id for tag linking after import upsert."""
    if not keys:
        return {}
    # SQLite variable limit: chunk large key lists (2 binds per key).
    out: dict[tuple[int, int], int] = {}
    chunk_size = 450
    for offset in range(0, len(keys), chunk_size):
        chunk = keys[offset : offset + chunk_size]
        rows = (
            await session.execute(
                select(Image.id, Image.illust_id, Image.page_index).where(
                    tuple_(Image.illust_id, Image.page_index).in_(chunk)
                )
            )
        ).all()
        for img_id, illust_id, page_index in rows:
            out[(int(illust_id), int(page_index))] = int(img_id)
    return out

