from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.images_upsert import dialect_name_from_session, insert_for_dialect
from app.db.models.image_tags import ImageTag
from app.db.models.tags import Tag


async def image_has_any_tag(session: AsyncSession, *, image_id: int) -> bool:
    row = (
        await session.execute(
            sa.select(ImageTag.image_id).where(ImageTag.image_id == int(image_id)).limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def map_tag_names_by_image_ids(
    session: AsyncSession,
    *,
    image_ids: Sequence[int],
) -> dict[int, list[str]]:
    ids = [int(i) for i in image_ids]
    out: dict[int, list[str]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = (
        await session.execute(
            sa.select(ImageTag.image_id, Tag.name)
            .join(Tag, Tag.id == ImageTag.tag_id)
            .where(ImageTag.image_id.in_(ids))
        )
    ).all()
    for image_id, name in rows:
        n = str(name or "").strip()
        if not n:
            continue
        out.setdefault(int(image_id), []).append(n)
    return out


async def delete_image_tags_for_image_ids(
    session: AsyncSession,
    *,
    image_ids: Sequence[int],
) -> int:
    ids = [int(i) for i in image_ids if int(i) > 0]
    if not ids:
        return 0
    deleted = 0
    # Chunk for SQLite variable limits.
    for offset in range(0, len(ids), 900):
        chunk = ids[offset : offset + 900]
        result = await session.execute(sa.delete(ImageTag).where(ImageTag.image_id.in_(chunk)))
        try:
            deleted += int(getattr(result, "rowcount", 0) or 0)
        except Exception:
            pass
    return deleted


async def clear_all_image_tags(session: AsyncSession) -> int:
    result = await session.execute(sa.delete(ImageTag))
    try:
        return int(getattr(result, "rowcount", 0) or 0)
    except Exception:
        return 0


async def clear_all_tags(session: AsyncSession) -> int:
    result = await session.execute(sa.delete(Tag))
    try:
        return int(getattr(result, "rowcount", 0) or 0)
    except Exception:
        return 0


async def ensure_tags_by_names(
    session: AsyncSession,
    *,
    names: Sequence[str],
) -> dict[str, int]:
    """Insert missing Tag rows by name (no translation); return name → id for all names."""
    clean: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        clean.append(name)
    if not clean:
        return {}

    dialect = dialect_name_from_session(session)
    insert = insert_for_dialect(Tag, dialect_name=dialect)
    stmt = insert.values([{"name": n, "translated_name": None} for n in clean])
    stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
    await session.execute(stmt)

    rows = (await session.execute(sa.select(Tag.id, Tag.name).where(Tag.name.in_(clean)))).all()
    return {str(name): int(tag_id) for (tag_id, name) in rows}


async def upsert_tags_with_translations(
    session: AsyncSession,
    *,
    tags: Sequence[tuple[str, str | None]],
    now: str | None = None,
) -> dict[str, int]:
    """Ensure Tag rows exist; optionally update translated_name. Returns name → id."""
    from app.core.time import iso_utc_ms

    clean: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for raw_name, raw_tr in tags:
        name = str(raw_name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        tr = str(raw_tr).strip() if raw_tr is not None and str(raw_tr).strip() else None
        clean.append((name, tr))
    if not clean:
        return {}

    names = [n for n, _ in clean]
    rows = (await session.execute(sa.select(Tag).where(Tag.name.in_(names)))).scalars().all()
    existing = {str(t.name): t for t in rows}

    tag_ids: dict[str, int] = {}
    stamp = now if now is not None else iso_utc_ms()
    for name, translated in clean:
        row = existing.get(name)
        if row is None:
            row = Tag(name=name, translated_name=translated)
            session.add(row)
            await session.flush()
            existing[name] = row
        else:
            if translated is not None and translated != row.translated_name:
                row.translated_name = translated
                row.updated_at = stamp
        tag_ids[name] = int(row.id)
    return tag_ids


async def link_image_tags(
    session: AsyncSession,
    *,
    pairs: Sequence[tuple[int, int]],
) -> int:
    """Insert (image_id, tag_id) links; ignore conflicts. Returns attempted pair count."""
    values: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for image_id, tag_id in pairs:
        key = (int(image_id), int(tag_id))
        if key[0] <= 0 or key[1] <= 0 or key in seen:
            continue
        seen.add(key)
        values.append({"image_id": key[0], "tag_id": key[1]})
    if not values:
        return 0
    dialect = dialect_name_from_session(session)
    insert = insert_for_dialect(ImageTag, dialect_name=dialect)
    for offset in range(0, len(values), 5000):
        sub = values[offset : offset + 5000]
        stmt = insert.values(sub).on_conflict_do_nothing(index_elements=["image_id", "tag_id"])
        await session.execute(stmt)
    return len(values)


async def replace_image_tags(
    session: AsyncSession,
    *,
    image_ids: Sequence[int],
    tag_ids: Sequence[int],
) -> None:
    """Delete all links for image_ids, then attach every tag_id to every image_id."""
    ids = [int(i) for i in image_ids if int(i) > 0]
    if not ids:
        return
    await delete_image_tags_for_image_ids(session, image_ids=ids)
    t_ids = [int(t) for t in tag_ids if int(t) > 0]
    if not t_ids:
        return
    pairs = [(img_id, tag_id) for img_id in ids for tag_id in t_ids]
    await link_image_tags(session, pairs=pairs)
