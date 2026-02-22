from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.images import Image
from app.db.sqlite_utils import sqlite_fts_phrase_query, sqlite_table_exists


@dataclass(frozen=True, slots=True)
class AuthorListItem:
    user_id: int
    user_name: str | None
    count_images: int


async def list_authors(
    session: AsyncSession,
    *,
    limit: int,
    cursor: int | None = None,
    q: str | None = None,
) -> tuple[list[AuthorListItem], int | None]:
    limit_i = int(limit)
    if limit_i < 1:
        raise ValueError("limit must be >= 1")

    cursor_i: int | None = None
    if cursor is not None:
        cursor_i = int(cursor)
        if cursor_i <= 0:
            raise ValueError("cursor must be > 0")

    q_norm = (q or "").strip()
    use_fts = False
    fts_q = ""
    if q_norm and len(q_norm) >= 3:
        use_fts = await sqlite_table_exists(session, name="authors_fts")
        if use_fts:
            fts_q = sqlite_fts_phrase_query(q_norm)

    def _build_stmt(*, use_fts_filter: bool) -> sa.Select:
        clauses: list[object] = [Image.status == 1, Image.user_id.is_not(None)]

        if cursor_i is not None:
            clauses.append(Image.user_id > cursor_i)

        if q_norm:
            if use_fts_filter:
                fts_user_ids = (
                    sa.text("SELECT rowid AS user_id FROM authors_fts WHERE authors_fts MATCH :q")
                    .bindparams(sa.bindparam("q", fts_q))
                    .columns(user_id=sa.Integer)
                )
                fts_user_ids_sq = fts_user_ids.subquery()
                clauses.append(Image.user_id.in_(sa.select(fts_user_ids_sq.c.user_id)))
            else:
                clauses.append(Image.user_name.like(f"%{q_norm}%"))

        return (
            select(
                Image.user_id,
                func.max(Image.user_name).label("user_name"),
                func.count(Image.id).label("count_images"),
            )
            .where(*clauses)
            .group_by(Image.user_id)
            .order_by(Image.user_id.asc())
            .limit(limit_i + 1)
        )

    stmt = _build_stmt(use_fts_filter=use_fts)
    try:
        rows = (await session.execute(stmt)).all()
    except DBAPIError:
        if not use_fts:
            raise
        stmt = _build_stmt(use_fts_filter=False)
        rows = (await session.execute(stmt)).all()
    next_cursor: int | None = None

    if len(rows) > limit_i:
        rows = rows[:limit_i]
        next_cursor = int(rows[-1][0] or 0)

    items = [
        AuthorListItem(
            user_id=int(row[0]),
            user_name=str(row[1]) if row[1] is not None else None,
            count_images=int(row[2] or 0),
        )
        for row in rows
    ]

    return items, next_cursor
