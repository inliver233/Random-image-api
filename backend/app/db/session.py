from __future__ import annotations

import asyncio
import random
import sqlite3
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy.exc import OperationalError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.env_parse import parse_float_env, parse_int_env

T = TypeVar("T")

# One sessionmaker per AsyncEngine process-wide (public hot path).
_SESSIONMAKER_BY_ENGINE: weakref.WeakKeyDictionary[AsyncEngine, async_sessionmaker[AsyncSession]] = (
    weakref.WeakKeyDictionary()
)


def _is_transient_contention_message(msg: str) -> bool:
    """SQLite busy + common Postgres contention strings (asyncpg/psycopg)."""
    text = (msg or "").lower()
    if not text:
        return False
    sqlite_hits = (
        "database is locked",
        "database table is locked",
        "database schema is locked",
        "database is busy",
    )
    if any(h in text for h in sqlite_hits):
        return True
    # Postgres: retry-safe contention / serialization (no silent swallow of hard errors).
    pg_hits = (
        "deadlock detected",
        "could not serialize access",
        "canceling statement due to lock timeout",
        "lock_not_available",
        "tuple concurrently updated",
    )
    return any(h in text for h in pg_hits)


def is_sqlite_busy_error(exc: BaseException) -> bool:
    """True for transient DB contention worth retrying (SQLite busy + PG deadlock/serialize).

    Name is historical (SQLite-first); Postgres messages are included so
    ``with_sqlite_busy_retry`` remains useful after dialect cutover without a
    second wrapper. Hard failures must not match.
    """
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, sqlite3.OperationalError):
        return _is_transient_contention_message(str(exc))
    if isinstance(exc, OperationalError):
        orig = getattr(exc, "orig", None)
        if isinstance(orig, BaseException) and is_sqlite_busy_error(orig):
            return True
        return _is_transient_contention_message(str(exc))
    # Bare driver errors sometimes surface without SQLAlchemy wrap (asyncpg).
    if isinstance(exc, Exception) and not isinstance(exc, (KeyboardInterrupt, SystemExit)):
        # Only treat known contention strings — do not retry arbitrary Exception.
        if type(exc).__module__.startswith(("asyncpg", "psycopg", "psycopg2")) or type(
            exc
        ).__name__ in {"DeadlockDetectedError", "SerializationError", "LockNotAvailableError"}:
            return _is_transient_contention_message(str(exc))
    return False


# Alias for callers that want dialect-neutral naming (same predicate).
is_transient_db_contention_error = is_sqlite_busy_error


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


def resolve_sessionmaker(request: Any, engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    """Prefer ``app.state.sessionmaker`` (wired at startup); fall back to process cache.

    Public and admin routes share this so DI stays one path without re-allocating
    when state is present.
    """
    # Local import keeps session.py free of FastAPI at module import for pure DB tests.
    eng = engine
    if eng is None:
        eng = getattr(getattr(request, "app", None), "state", None)
        eng = getattr(eng, "engine", None) if eng is not None else None
    state_sm = getattr(getattr(getattr(request, "app", None), "state", None), "sessionmaker", None)
    if state_sm is not None:
        return state_sm
    if eng is None:
        raise RuntimeError("resolve_sessionmaker requires app.state.engine or engine=")
    return create_sessionmaker(eng)


async def get_session(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session
