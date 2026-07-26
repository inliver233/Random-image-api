from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.tags import Tag
from app.db.sqlite_utils import sqlite_fts_phrase_query, sqlite_table_exists


@dataclass(frozen=True, slots=True)
class TagListItem:
    id: int
    name: str
    translated_name: str | None
    count_images: int


def _active_image_tag_exists() -> sa.Exists:
    """EXISTS: tag has ≥1 link to status=1 image (same membership as pre-TAGS-1 INNER JOIN)."""
    return (
        sa.exists(
            select(1)
            .select_from(ImageTag)
            .join(Image, Image.id == ImageTag.image_id)
            .where(ImageTag.tag_id == Tag.id, Image.status == 1)
        )
    )


async def list_tags(
    session: AsyncSession,
    *,
    limit: int,
    cursor: str | None = None,
    q: str | None = None,
) -> tuple[list[TagListItem], str | None]:
    """Cursor-paginated tags with active-image counts.

    TAGS-1: page tags first (name order + optional q), then COUNT only for that page.
    Avoids full join+GROUP BY over all image_tags on every /tags request (~14s on 62万图).
    """
    limit_i = int(limit)
    if limit_i < 1:
        raise ValueError("limit must be >= 1")

    cursor_name = (cursor or "").strip()
    cursor_name = cursor_name if cursor_name else ""

    q_norm = (q or "").strip()

    use_fts = False
    fts_q = ""
    if q_norm and len(q_norm) >= 3:
        use_fts = await sqlite_table_exists(session, name="tags_fts")
        if use_fts:
            fts_q = sqlite_fts_phrase_query(q_norm)

    def _build_page_stmt(*, use_fts_filter: bool) -> sa.Select:
        # Page candidates only — no aggregate over the full link table.
        stmt = select(Tag.id, Tag.name, Tag.translated_name).where(_active_image_tag_exists())

        if q_norm:
            if use_fts_filter:
                fts_ids = (
                    sa.text("SELECT rowid AS tag_id FROM tags_fts WHERE tags_fts MATCH :q")
                    .bindparams(sa.bindparam("q", fts_q))
                    .columns(tag_id=sa.Integer)
                )
                fts_ids_sq = fts_ids.subquery()
                stmt = stmt.where(Tag.id.in_(sa.select(fts_ids_sq.c.tag_id)))
            else:
                # ilike keeps SQLite's default case-insensitive LIKE semantics
                # on PostgreSQL too (plain LIKE is case-sensitive there); the
                # pg_trgm GIN indexes from migration 0023 serve %…% ILIKE.
                like = f"%{q_norm}%"
                stmt = stmt.where(sa.or_(Tag.name.ilike(like), Tag.translated_name.ilike(like)))
        if cursor_name:
            stmt = stmt.where(Tag.name > cursor_name)

        return stmt.order_by(Tag.name.asc()).limit(limit_i + 1)

    page_stmt = _build_page_stmt(use_fts_filter=use_fts)
    try:
        page_rows = (await session.execute(page_stmt)).all()
    except DBAPIError:
        if not use_fts:
            raise
        page_stmt = _build_page_stmt(use_fts_filter=False)
        page_rows = (await session.execute(page_stmt)).all()

    next_cursor: str | None = None
    if len(page_rows) > limit_i:
        page_rows = page_rows[:limit_i]
        next_cursor = str(page_rows[-1][1] or "")

    if not page_rows:
        return [], None

    tag_ids = [int(row[0]) for row in page_rows]
    # PK (image_id, tag_id) → COUNT(*) is enough; status=1 filter matches prior semantics.
    count_rows = (
        await session.execute(
            select(ImageTag.tag_id, func.count())
            .join(Image, Image.id == ImageTag.image_id)
            .where(ImageTag.tag_id.in_(tag_ids), Image.status == 1)
            .group_by(ImageTag.tag_id)
        )
    ).all()
    counts = {int(tag_id): int(cnt or 0) for tag_id, cnt in count_rows}

    items = [
        TagListItem(
            id=int(row[0]),
            name=str(row[1]),
            translated_name=str(row[2]) if row[2] is not None else None,
            count_images=int(counts.get(int(row[0]), 0)),
        )
        for row in page_rows
    ]

    return items, next_cursor
