from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import distinct, func, select
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


async def list_tags(
    session: AsyncSession,
    *,
    limit: int,
    cursor: str | None = None,
    q: str | None = None,
) -> tuple[list[TagListItem], str | None]:
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

    def _build_stmt(*, use_fts_filter: bool) -> sa.Select:
        stmt = (
            select(
                Tag.id,
                Tag.name,
                Tag.translated_name,
                func.count(distinct(ImageTag.image_id)).label("count_images"),
            )
            .join(ImageTag, ImageTag.tag_id == Tag.id)
            .join(Image, Image.id == ImageTag.image_id)
            .where(Image.status == 1)
        )

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
                like = f"%{q_norm}%"
                stmt = stmt.where(sa.or_(Tag.name.like(like), Tag.translated_name.like(like)))
        if cursor_name:
            stmt = stmt.where(Tag.name > cursor_name)

        return stmt.group_by(Tag.id).order_by(Tag.name.asc()).limit(limit_i + 1)

    stmt = _build_stmt(use_fts_filter=use_fts)
    try:
        rows = (await session.execute(stmt)).all()
    except DBAPIError:
        if not use_fts:
            raise
        stmt = _build_stmt(use_fts_filter=False)
        rows = (await session.execute(stmt)).all()
    next_cursor: str | None = None

    if len(rows) > limit_i:
        rows = rows[:limit_i]
        next_cursor = str(rows[-1][1] or "")

    items = [
        TagListItem(
            id=int(row[0]),
            name=str(row[1]),
            translated_name=str(row[2]) if row[2] is not None else None,
            count_images=int(row[3] or 0),
        )
        for row in rows
    ]

    return items, next_cursor
