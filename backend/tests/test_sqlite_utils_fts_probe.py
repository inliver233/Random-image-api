from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.db.sqlite_utils import sqlite_fts_phrase_query, sqlite_table_exists


def test_sqlite_fts_phrase_query_escapes_quotes() -> None:
    assert sqlite_fts_phrase_query('a"b') == '"a""b"'
    assert sqlite_fts_phrase_query("  cat  ") == '"cat"'


def test_sqlite_table_exists_false_on_postgres_without_sqlite_master() -> None:
    session = MagicMock()
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    session.get_bind.return_value = bind
    session.execute = AsyncMock()

    ok = asyncio.run(sqlite_table_exists(session, name="tags_fts"))
    assert ok is False
    session.execute.assert_not_awaited()


def test_sqlite_table_exists_false_on_empty_dialect_name() -> None:
    session = MagicMock()
    bind = SimpleNamespace(dialect=SimpleNamespace(name=""))
    session.get_bind.return_value = bind
    session.execute = AsyncMock()

    ok = asyncio.run(sqlite_table_exists(session, name="tags_fts"))
    assert ok is False
    session.execute.assert_not_awaited()


def test_sqlite_table_exists_queries_master_on_sqlite() -> None:
    session = MagicMock()
    bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    session.get_bind.return_value = bind
    result = MagicMock()
    result.first.return_value = (1,)
    session.execute = AsyncMock(return_value=result)

    ok = asyncio.run(sqlite_table_exists(session, name="tags_fts"))
    assert ok is True
    session.execute.assert_awaited_once()
