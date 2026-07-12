from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.coerce import truncate_text
from app.core.redact import redact_text
from app.db.images_upsert import dialect_name_from_session, now_expr_for_dialect
from app.db.models.images import Image
from app.db.session import create_sessionmaker, with_sqlite_busy_retry


async def mark_image_failure(
    engine: AsyncEngine,
    *,
    image_id: int,
    now: str,
    error_code: str,
    error_message: str,
) -> None:
    Session = create_sessionmaker(engine)
    msg = truncate_text(redact_text(error_message or ""))

    async def _op() -> None:
        async with Session() as session:
            await session.execute(
                update(Image)
                .where(Image.id == int(image_id))
                .values(
                    fail_count=Image.fail_count + 1,
                    last_fail_at=str(now),
                    last_error_code=str(error_code),
                    last_error_msg=str(msg),
                )
            )
            await session.commit()

    await with_sqlite_busy_retry(_op)


async def mark_image_ok(engine: AsyncEngine, *, image_id: int, now: str) -> None:
    Session = create_sessionmaker(engine)

    async def _op() -> None:
        async with Session() as session:
            await session.execute(
                update(Image)
                .where(Image.id == int(image_id))
                .values(
                    last_ok_at=str(now),
                    last_error_code=None,
                    last_error_msg=None,
                )
            )
            await session.commit()

    await with_sqlite_busy_retry(_op)


async def heal_broken_images_for_illust(
    session: AsyncSession,
    *,
    illust_id: int,
    now: str,
) -> list[int]:
    """Set status 3 → 1 for all pages of an illust after successful hydrate heal.

    Caller owns commit. Returns healed image ids (for engine re-index / R2 prewarm).
    """
    rows = (
        await session.execute(
            select(Image.id).where(Image.illust_id == int(illust_id)).where(Image.status == 3)
        )
    ).scalars().all()
    healed_ids = [int(x) for x in rows]
    if healed_ids:
        await session.execute(
            update(Image)
            .where(Image.id.in_(healed_ids))
            .values(
                status=1,
                last_ok_at=str(now),
                last_error_code=None,
                last_error_msg=None,
                updated_at=str(now),
            )
        )
    return healed_ids


async def set_status_for_import(
    session: AsyncSession,
    *,
    import_id: int,
    status: int,
    now_expr: object | None = None,
) -> int:
    """Bulk-set status for all images created by an import (admin rollback).

    Caller owns commit. Returns rowcount when available (0 if unknown).
    """
    values: dict[str, object] = {"status": int(status)}
    if now_expr is not None:
        values["updated_at"] = now_expr
    else:
        values["updated_at"] = now_expr_for_dialect(dialect_name_from_session(session))
    result = await session.execute(
        update(Image).where(Image.created_import_id == int(import_id)).values(**values)
    )
    try:
        rc = int(getattr(result, "rowcount", 0) or 0)
    except Exception:
        return 0
    return rc if rc > 0 else 0

