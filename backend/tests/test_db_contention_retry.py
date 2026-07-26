"""Transient DB contention predicate (H6).

SQLAlchemy wraps asyncpg's DeadlockDetectedError/SerializationError as a
generic ``DBAPIError`` — not ``OperationalError`` — so the old predicate
(which only unwrapped OperationalError) never retried the most common
wrapped PostgreSQL contention path. The predicate now inspects the
driver SQLSTATE (40001 serialization_failure, 40P01 deadlock_detected,
55P03 lock_not_available) on both wrapped and bare driver errors.
"""

from __future__ import annotations

import asyncio
import sqlite3

import asyncpg.exceptions as pg_exc
import pytest
from sqlalchemy.exc import DBAPIError, OperationalError

from app.db.session import is_sqlite_busy_error, with_sqlite_busy_retry


def _wrap(exc: BaseException) -> DBAPIError:
    wrapped = DBAPIError.instance("SELECT 1", (), exc, Exception, dialect=None)
    assert isinstance(wrapped, DBAPIError)
    return wrapped


def test_wrapped_asyncpg_deadlock_is_retryable() -> None:
    exc = pg_exc.DeadlockDetectedError("deadlock detected")
    wrapped = _wrap(exc)
    assert type(wrapped) is DBAPIError  # not OperationalError: the old gap
    assert exc.sqlstate == "40P01"
    assert is_sqlite_busy_error(wrapped) is True


def test_wrapped_asyncpg_serialization_failure_is_retryable() -> None:
    exc = pg_exc.SerializationError("could not serialize access due to concurrent update")
    wrapped = _wrap(exc)
    assert exc.sqlstate == "40001"
    assert is_sqlite_busy_error(wrapped) is True


def test_wrapped_asyncpg_lock_not_available_is_retryable() -> None:
    exc = pg_exc.LockNotAvailableError("could not obtain lock")
    wrapped = _wrap(exc)
    assert exc.sqlstate == "55P03"
    assert is_sqlite_busy_error(wrapped) is True


def test_bare_asyncpg_deadlock_is_retryable() -> None:
    assert is_sqlite_busy_error(pg_exc.DeadlockDetectedError("deadlock detected")) is True


def test_wrapped_hard_errors_are_not_retryable() -> None:
    # Constraint violations and syntax errors must fail loud, not retry.
    unique = pg_exc.UniqueViolationError("duplicate key value violates unique constraint")
    assert is_sqlite_busy_error(_wrap(unique)) is False
    syntax = pg_exc.PostgresSyntaxError("syntax error at or near")
    assert is_sqlite_busy_error(_wrap(syntax)) is False
    assert is_sqlite_busy_error(ValueError("boom")) is False


def test_sqlite_busy_still_retryable() -> None:
    raw = sqlite3.OperationalError("database is locked")
    assert is_sqlite_busy_error(raw) is True
    wrapped = OperationalError("SELECT 1", (), raw)
    assert is_sqlite_busy_error(wrapped) is True


def test_retry_wrapper_retries_wrapped_deadlock_then_succeeds(monkeypatch) -> None:
    monkeypatch.setenv("SQLITE_BUSY_BASE_DELAY_S", "0")
    monkeypatch.setenv("SQLITE_BUSY_MAX_DELAY_S", "0")
    attempts = 0

    async def _op() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _wrap(pg_exc.DeadlockDetectedError("deadlock detected"))
        return "ok"

    assert asyncio.run(with_sqlite_busy_retry(_op)) == "ok"
    assert attempts == 3


def test_retry_wrapper_does_not_retry_wrapped_unique_violation(monkeypatch) -> None:
    monkeypatch.setenv("SQLITE_BUSY_BASE_DELAY_S", "0")
    attempts = 0

    async def _op() -> None:
        nonlocal attempts
        attempts += 1
        raise _wrap(pg_exc.UniqueViolationError("duplicate key value violates unique constraint"))

    with pytest.raises(DBAPIError):
        asyncio.run(with_sqlite_busy_retry(_op))
    assert attempts == 1
