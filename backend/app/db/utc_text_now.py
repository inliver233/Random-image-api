"""Dialect-aware UTC ISO text timestamp expressions (string columns).

Shared by runtime upserts, ORM ``server_default``, and alembic migrations so
SQLite and Postgres emit the same shape: ``YYYY-MM-DDTHH:MM:SS.mmmZ``.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement


SQLITE_UTC_TEXT_NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
# Approximate SQLite strftime without pgcrypto; millisecond precision via MS.
POSTGRES_UTC_TEXT_NOW = (
    "(to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD\"T\"HH24:MI:SS.MS') || 'Z')"
)


class UtcNow(FunctionElement):
    """Portable server_default / column default for Text ISO-UTC timestamps."""

    type = sa.Text()
    inherit_cache = True
    name = "ria_utc_now"


@compiles(UtcNow, "sqlite")
def _compile_utc_now_sqlite(_element: UtcNow, _compiler: object, **_kw: object) -> str:
    return SQLITE_UTC_TEXT_NOW


@compiles(UtcNow, "postgresql")
def _compile_utc_now_postgresql(_element: UtcNow, _compiler: object, **_kw: object) -> str:
    return POSTGRES_UTC_TEXT_NOW


@compiles(UtcNow)
def _compile_utc_now_default(_element: UtcNow, _compiler: object, **_kw: object) -> str:
    # Unknown dialect → SQLite shape (dev default).
    return SQLITE_UTC_TEXT_NOW


def utc_iso_now_sql(dialect_name: str | None = None) -> str:
    """Return raw SQL fragment (with outer parens) for server_default / SET."""
    name = (dialect_name or "sqlite").strip().lower() or "sqlite"
    if name.startswith("postgres"):
        return POSTGRES_UTC_TEXT_NOW
    return SQLITE_UTC_TEXT_NOW


def utc_iso_now_server_default(dialect_name: str | None = None) -> sa.TextClause:
    """``server_default=…`` when dialect is known at call time (alembic)."""
    return sa.text(utc_iso_now_sql(dialect_name))


def utc_iso_now_expr(dialect_name: str | None = None) -> sa.TextClause:
    """Expression for ``UPDATE … SET updated_at=…`` (same SQL as server default)."""
    return sa.text(utc_iso_now_sql(dialect_name))
