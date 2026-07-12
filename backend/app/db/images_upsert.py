from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.images import Image


def dialect_name_from_session(session: AsyncSession) -> str:
    """Best-effort SQLAlchemy dialect name from the bound session (sqlite default)."""
    try:
        bind = session.get_bind()
    except Exception:
        return "sqlite"
    name = getattr(getattr(bind, "dialect", None), "name", None)
    text = str(name or "sqlite").strip().lower()
    return text or "sqlite"


def insert_for_dialect(table: Any, *, dialect_name: str) -> Any:
    """Dialect-aware INSERT … ON CONFLICT builder (sqlite | postgresql)."""
    name = (dialect_name or "sqlite").strip().lower() or "sqlite"
    if name.startswith("postgres"):
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert(table)
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    return sqlite_insert(table)


def now_expr_for_dialect(dialect_name: str) -> Any:
    """UTC ISO-ish timestamp expression for updated_at (string column parity)."""
    name = (dialect_name or "sqlite").strip().lower() or "sqlite"
    if name.startswith("postgres"):
        # Approximate SQLite strftime('%Y-%m-%dT%H:%M:%fZ','now') without requiring pgcrypto.
        return sa.text(
            "(to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD\"T\"HH24:MI:SS.MS') || 'Z')"
        )
    return sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))")


def dialect_name_from_engine(engine: Any) -> str:
    """Best-effort SQLAlchemy dialect name from an AsyncEngine/Engine (sqlite default)."""
    try:
        name = getattr(getattr(engine, "dialect", None), "name", None)
    except Exception:
        return "sqlite"
    text = str(name or "sqlite").strip().lower()
    return text or "sqlite"


def driver_param_marker(dialect_name: str) -> str:
    """Positional bind marker for raw `exec_driver_sql` (sqlite `?` | postgres `%s`)."""
    name = (dialect_name or "sqlite").strip().lower() or "sqlite"
    if name.startswith("postgres"):
        return "%s"
    return "?"


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
    dialect = dialect_name_from_session(session)
    insert = insert_for_dialect(Image, dialect_name=dialect)
    stmt = insert.values(
        illust_id=illust_id,
        page_index=page_index,
        ext=ext,
        original_url=original_url,
        proxy_path=proxy_path,
        random_key=random_key,
        created_import_id=created_import_id,
    )

    now_expr = now_expr_for_dialect(dialect)
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


async def upsert_hydrated_image_page(
    session: AsyncSession,
    *,
    illust_id: int,
    page_index: int,
    ext: str,
    original_url: str,
    random_key: float,
    width: int | None,
    height: int | None,
    aspect_ratio: float | None,
    orientation: int | None,
    x_restrict: int | None,
    ai_type: int | None,
    illust_type: int | None,
    user_id: int | None,
    user_name: str | None,
    title: str | None,
    created_at_pixiv: str | None,
    bookmark_count: int | None,
    view_count: int | None,
    comment_count: int | None,
    created_import_id: int | None,
) -> int:
    """Upsert one page with hydrate metadata; set proxy_path from returned id.

    Does not touch tags (ImageTag) — callers replace tags separately.
    On conflict, preserves existing random_key / status / fail counters.
    """
    dialect = dialect_name_from_session(session)
    insert = insert_for_dialect(Image, dialect_name=dialect)
    now_expr = now_expr_for_dialect(dialect)
    stmt = insert.values(
        illust_id=int(illust_id),
        page_index=int(page_index),
        ext=str(ext),
        original_url=str(original_url),
        proxy_path="",
        random_key=float(random_key),
        width=width,
        height=height,
        aspect_ratio=aspect_ratio,
        orientation=orientation,
        x_restrict=x_restrict,
        ai_type=ai_type,
        illust_type=illust_type,
        user_id=user_id,
        user_name=user_name,
        title=title,
        created_at_pixiv=created_at_pixiv,
        bookmark_count=bookmark_count,
        view_count=view_count,
        comment_count=comment_count,
        created_import_id=int(created_import_id) if created_import_id else None,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["illust_id", "page_index"],
        set_={
            "ext": stmt.excluded.ext,
            "original_url": stmt.excluded.original_url,
            "width": stmt.excluded.width,
            "height": stmt.excluded.height,
            "aspect_ratio": stmt.excluded.aspect_ratio,
            "orientation": stmt.excluded.orientation,
            "x_restrict": stmt.excluded.x_restrict,
            "ai_type": stmt.excluded.ai_type,
            "illust_type": stmt.excluded.illust_type,
            "user_id": stmt.excluded.user_id,
            "user_name": stmt.excluded.user_name,
            "title": stmt.excluded.title,
            "created_at_pixiv": stmt.excluded.created_at_pixiv,
            "bookmark_count": stmt.excluded.bookmark_count,
            "view_count": stmt.excluded.view_count,
            "comment_count": stmt.excluded.comment_count,
            "updated_at": now_expr,
        },
    ).returning(Image.id)

    result = await session.execute(stmt)
    image_id = int(result.scalar_one())
    proxy_path = f"/i/{image_id}.{ext}"
    await session.execute(sa.update(Image).where(Image.id == image_id).values(proxy_path=proxy_path))
    return image_id


async def bulk_upsert_import_rows(
    session: AsyncSession,
    *,
    rows: list[dict],
    keys: list[tuple[int, int]],
    import_id: int,
) -> list[int]:
    """Bulk upsert import image rows with nullable CASE merges; fill empty proxy_path.

    Does not touch tags or Import counters — callers own those.
    Returns image ids for the given (illust_id, page_index) keys (for engine publish).
    """
    if not rows:
        return []

    dialect = dialect_name_from_session(session)
    insert = insert_for_dialect(Image, dialect_name=dialect)
    now_expr = now_expr_for_dialect(dialect)
    stmt = insert.values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["illust_id", "page_index"],
        set_={
            "ext": stmt.excluded.ext,
            "original_url": stmt.excluded.original_url,
            "proxy_path": sa.case(
                (sa.func.length(stmt.excluded.proxy_path) > 0, stmt.excluded.proxy_path),
                else_=Image.proxy_path,
            ),
            "created_import_id": stmt.excluded.created_import_id,
            "width": sa.case((stmt.excluded.width.is_not(None), stmt.excluded.width), else_=Image.width),
            "height": sa.case((stmt.excluded.height.is_not(None), stmt.excluded.height), else_=Image.height),
            "aspect_ratio": sa.case(
                (stmt.excluded.aspect_ratio.is_not(None), stmt.excluded.aspect_ratio),
                else_=Image.aspect_ratio,
            ),
            "orientation": sa.case(
                (stmt.excluded.orientation.is_not(None), stmt.excluded.orientation),
                else_=Image.orientation,
            ),
            "x_restrict": sa.case(
                (stmt.excluded.x_restrict.is_not(None), stmt.excluded.x_restrict),
                else_=Image.x_restrict,
            ),
            "ai_type": sa.case((stmt.excluded.ai_type.is_not(None), stmt.excluded.ai_type), else_=Image.ai_type),
            "illust_type": sa.case(
                (stmt.excluded.illust_type.is_not(None), stmt.excluded.illust_type),
                else_=Image.illust_type,
            ),
            "user_id": sa.case((stmt.excluded.user_id.is_not(None), stmt.excluded.user_id), else_=Image.user_id),
            "user_name": sa.case(
                (stmt.excluded.user_name.is_not(None), stmt.excluded.user_name),
                else_=Image.user_name,
            ),
            "title": sa.case((stmt.excluded.title.is_not(None), stmt.excluded.title), else_=Image.title),
            "created_at_pixiv": sa.case(
                (stmt.excluded.created_at_pixiv.is_not(None), stmt.excluded.created_at_pixiv),
                else_=Image.created_at_pixiv,
            ),
            "bookmark_count": sa.case(
                (stmt.excluded.bookmark_count.is_not(None), stmt.excluded.bookmark_count),
                else_=Image.bookmark_count,
            ),
            "view_count": sa.case(
                (stmt.excluded.view_count.is_not(None), stmt.excluded.view_count),
                else_=Image.view_count,
            ),
            "comment_count": sa.case(
                (stmt.excluded.comment_count.is_not(None), stmt.excluded.comment_count),
                else_=Image.comment_count,
            ),
            "updated_at": now_expr,
        },
    )
    await session.execute(stmt)

    # Fill proxy_path for (newly inserted) rows that are still empty.
    # ``||`` string concat is portable across SQLite and Postgres text.
    if keys:
        await session.execute(
            sa.update(Image)
            .where(Image.created_import_id == int(import_id))
            .where(Image.proxy_path == "")
            .where(sa.tuple_(Image.illust_id, Image.page_index).in_(keys))
            .values(proxy_path=sa.text("'/i/' || id || '.' || ext"))
        )

        id_rows = (
            await session.execute(
                sa.select(Image.id).where(sa.tuple_(Image.illust_id, Image.page_index).in_(keys))
            )
        ).scalars().all()
        return [int(x) for x in id_rows]
    return []
