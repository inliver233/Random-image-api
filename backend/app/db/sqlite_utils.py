from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


def sqlite_fts_phrase_query(q: str) -> str:
    q = (q or "").strip()
    q = q.replace('"', '""')
    return f'"{q}"'


def _session_is_sqlite(session: AsyncSession) -> bool:
    """True only when the bound dialect is SQLite (never empty-name → True)."""
    try:
        bind = session.get_bind()
        name = str(getattr(getattr(bind, "dialect", None), "name", "") or "").strip().lower()
    except Exception:
        return False
    return name.startswith("sqlite")


async def sqlite_table_exists(session: AsyncSession, *, name: str) -> bool:
    """Probe whether a SQLite table/virtual table exists.

    On non-SQLite dialects (Postgres, …) always returns False so callers fall
    back to LIKE without ever issuing ``sqlite_master`` SQL.
    """
    name = (name or "").strip()
    if not name:
        return False
    if not _session_is_sqlite(session):
        return False
    result = await session.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name LIMIT 1;"),
        {"name": name},
    )
    return result.first() is not None
