from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.coerce import truncate_text
from app.core.redact import redact_text
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
    session,
    *,
    illust_id: int,
    now: str,
) -> list[int]:
    """Set status 3 → 1 for all pages of an illust after successful hydrate heal.

    Caller owns commit. Returns healed image ids (for engine re-index / R2 prewarm).
    """
    from sqlalchemy import select

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

