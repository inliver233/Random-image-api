from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from app.db.engine import SQLITE_BUSY_TIMEOUT_MS, create_engine
from app.db.session import is_sqlite_busy_error, with_sqlite_busy_retry


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


def test_sqlite_pragmas_applied(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    engine = create_engine(_sqlite_url(db_path))

    async def _check() -> None:
        async with engine.connect() as conn:
            fk = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar_one()
            assert int(fk) == 1

            journal = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar_one()
            assert str(journal).lower() == "wal"

            busy = (await conn.exec_driver_sql("PRAGMA busy_timeout")).scalar_one()
            assert int(busy) == SQLITE_BUSY_TIMEOUT_MS

            sync = (await conn.exec_driver_sql("PRAGMA synchronous")).scalar_one()
            assert int(sync) == 1

            temp_store = (await conn.exec_driver_sql("PRAGMA temp_store")).scalar_one()
            assert int(temp_store) == 2

        await engine.dispose()

    asyncio.run(_check())


def test_is_sqlite_busy_error() -> None:
    assert is_sqlite_busy_error(sqlite3.OperationalError("database is locked"))
    assert is_sqlite_busy_error(sqlite3.OperationalError("database table is locked"))
    assert not is_sqlite_busy_error(ValueError("nope"))


def test_with_sqlite_busy_retry_retries_then_succeeds() -> None:
    attempts = {"n": 0}

    async def _op() -> int:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return 123

    result = asyncio.run(with_sqlite_busy_retry(_op, retries=5, base_delay_s=0.0))
    assert result == 123
    assert attempts["n"] == 3

