from __future__ import annotations

import asyncio
import os
import random
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

T = TypeVar("T")


def is_sqlite_busy_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
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
    retries: int = 8,
    base_delay_s: float = 0.05,
) -> T:
    try:
        env_retries = int((os.environ.get("SQLITE_BUSY_RETRIES") or "").strip() or retries)
    except Exception:
        env_retries = int(retries)
    retries_i = max(0, min(int(env_retries), 50))

    try:
        env_base = float((os.environ.get("SQLITE_BUSY_BASE_DELAY_S") or "").strip() or base_delay_s)
    except Exception:
        env_base = float(base_delay_s)
    base_delay = float(max(0.0, min(float(env_base), 5.0)))

    try:
        env_max_delay = float((os.environ.get("SQLITE_BUSY_MAX_DELAY_S") or "").strip() or 2.0)
    except Exception:
        env_max_delay = 2.0
    max_delay = float(max(0.0, min(float(env_max_delay), 30.0)))

    attempt = 0
    while True:
        try:
            return await op()
        except Exception as exc:
            if attempt >= retries_i or not is_sqlite_busy_error(exc):
                raise
            delay = base_delay * (2**attempt)
            if max_delay > 0:
                delay = min(float(delay), float(max_delay))
            if delay > 0:
                # Add a tiny jitter to avoid synchronized retries under load.
                delay *= 0.9 + (random.random() * 0.2)
                await asyncio.sleep(float(delay))
            attempt += 1


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session
