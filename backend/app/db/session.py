from __future__ import annotations

import asyncio
import random
import sqlite3
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.env_parse import parse_float_env, parse_int_env

T = TypeVar("T")

# One sessionmaker per AsyncEngine process-wide (public hot path).
_SESSIONMAKER_BY_ENGINE: weakref.WeakKeyDictionary[AsyncEngine, async_sessionmaker[AsyncSession]] = (
    weakref.WeakKeyDictionary()
)


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
    retries: int = 12,
    base_delay_s: float = 0.05,
) -> T:
    retries_i = parse_int_env(
        "SQLITE_BUSY_RETRIES",
        default=int(retries),
        min_v=0,
        max_v=50,
    )
    base_delay = parse_float_env(
        "SQLITE_BUSY_BASE_DELAY_S",
        default=float(base_delay_s),
        min_v=0.0,
        max_v=5.0,
    )
    max_delay = parse_float_env(
        "SQLITE_BUSY_MAX_DELAY_S",
        default=5.0,
        min_v=0.0,
        max_v=30.0,
    )

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
    """Return a process-cached async_sessionmaker for the given engine.

    Call sites used to allocate a new sessionmaker per request; that is pure
    overhead. WeakKeyDictionary keeps one factory per live AsyncEngine.
    """
    cached = _SESSIONMAKER_BY_ENGINE.get(engine)
    if cached is not None:
        return cached
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    _SESSIONMAKER_BY_ENGINE[engine] = sm
    return sm


async def get_session(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session
