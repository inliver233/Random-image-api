"""Online alembic migrations must run on the installed async drivers (B2).

The production requirements ship ``asyncpg`` only — there is no sync
PostgreSQL driver anywhere in the dependency set. env.py used to strip
``+asyncpg`` from the URL, which made SQLAlchemy resolve the default
``psycopg2`` DBAPI and fail with ModuleNotFoundError before any
connection attempt. These tests pin the fixed behavior without needing
a live PostgreSQL: driver resolution must succeed and the failure (if
any) must be a connection failure, never a missing sync driver.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_upgrade_head(database_url: str) -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        command.upgrade(cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def test_alembic_online_postgres_asyncpg_resolves_driver_not_psycopg2() -> None:
    # Unreachable port: success criterion is an asyncpg CONNECTION failure,
    # proving the async online path used the installed async driver. Any
    # psycopg2 involvement (ModuleNotFoundError in the shipped image, or a
    # psycopg2 connection error on hosts that happen to have it) means the
    # URL was rewritten to a sync driver outside the dependency set.
    with pytest.raises(Exception) as excinfo:
        _run_upgrade_head("postgresql+asyncpg://ria:ria@127.0.0.1:1/random_image")

    chain: list[BaseException] = []
    exc: BaseException | None = excinfo.value
    while exc is not None and exc not in chain:
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    modules = [type(e).__module__ for e in chain]
    assert not any(isinstance(e, ModuleNotFoundError) for e in chain), (
        f"online migration tried to import a sync PostgreSQL driver: {[repr(e) for e in chain]}"
    )
    assert not any(m.startswith("psycopg2") for m in modules), (
        f"online migration connected through psycopg2 instead of asyncpg: {modules}"
    )
    assert any(
        m.startswith("asyncpg") or isinstance(e, (ConnectionError, OSError, TimeoutError, CommandError))
        for m, e in zip(modules, chain)
    ), f"expected an asyncpg connection-level failure, got: {[repr(e) for e in chain]}"


def test_alembic_online_sqlite_aiosqlite_upgrades_head(tmp_path: Path) -> None:
    db_path = tmp_path / "alembic_async_online.db"
    _run_upgrade_head("sqlite+aiosqlite:///" + db_path.as_posix())

    import sqlite3

    with sqlite3.connect(db_path) as conn:
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "images" in tables
    assert "alembic_version" in tables
