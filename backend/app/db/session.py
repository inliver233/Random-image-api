from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

T = TypeVar("T")


def is_sqlite_busy_error(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.OperationalError):
        msg = str(exc).lower()
        return (
            "database is locked" in msg
            or "database table is locked" in msg
            or "database schema is locked" in msg
            or "database is busy" in msg
        )
    if isinstance(exc, OperationalError):
        orig = getattr(exc, "orig", None)
        return is_sqlite_busy_error(orig) if isinstance(orig, BaseException) else False
    return False


async def with_sqlite_busy_retry(
    op: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
    base_delay_s: float = 0.05,
) -> T:
    attempt = 0
    while True:
        try:
            return await op()
        except Exception as exc:
            if attempt >= retries or not is_sqlite_busy_error(exc):
                raise
            await asyncio.sleep(base_delay_s * (2**attempt))
            attempt += 1


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session
