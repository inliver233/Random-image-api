from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.redact import redact_text
from app.db.models.images import Image
from app.db.session import create_sessionmaker, with_sqlite_busy_retry


def _truncate(text: str, *, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


async def mark_image_failure(
    engine: AsyncEngine,
    *,
    image_id: int,
    now: str,
    error_code: str,
    error_message: str,
) -> None:
    Session = create_sessionmaker(engine)
    msg = _truncate(redact_text(error_message or ""))

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

