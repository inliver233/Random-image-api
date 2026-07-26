from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    # Keep application loggers (e.g. uvicorn.*) alive when migrations run
    # in-process; the default disable_existing_loggers=True silences them.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.data_files import ensure_sqlite_parent_dir  # noqa: E402
from app.db.models.base import Base  # noqa: E402

# Real metadata enables autogenerate comparison and schema-drift checks (M4).
# Migrations remain the source of truth for upgrades; metadata must match them.
target_metadata = Base.metadata


def _get_database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL (or sqlalchemy.url) is required")
    return url


def _offline_url(url: str) -> str:
    # Offline mode only renders SQL from the dialect; no DBAPI is imported,
    # so the async driver suffix is irrelevant and stripped for clarity.
    for async_suffix in ("+aiosqlite", "+asyncpg", "+psycopg"):
        url = url.replace(async_suffix, "")
    return url


def _online_url(url: str) -> str:
    # Online migrations run through SQLAlchemy's async engine because the
    # dependency set ships async drivers only (aiosqlite/asyncpg). Bare URLs
    # default to those drivers; explicit "+driver" choices are respected.
    if url.startswith("sqlite://"):
        return "sqlite+aiosqlite://" + url[len("sqlite://") :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    return url


def run_migrations_offline() -> None:
    url = _offline_url(_get_database_url())
    ensure_sqlite_parent_dir(url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _online_url(_get_database_url())
    ensure_sqlite_parent_dir(url)

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        url=url,
        poolclass=pool.NullPool,
    )

    async def _run() -> None:
        try:
            async with connectable.connect() as connection:
                await connection.run_sync(_run_migrations)
        finally:
            await connectable.dispose()

    asyncio.run(_run())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
