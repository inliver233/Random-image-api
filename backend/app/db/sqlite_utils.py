from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


def sqlite_fts_phrase_query(q: str) -> str:
    q = (q or "").strip()
    q = q.replace('"', '""')
    return f'"{q}"'


async def sqlite_table_exists(session: AsyncSession, *, name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False
    result = await session.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name LIMIT 1;"),
        {"name": name},
    )
    return result.first() is not None
