from __future__ import annotations

import os
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

SQLITE_BUSY_TIMEOUT_MS = 30_000
SQLITE_POOL_SIZE = 5
SQLITE_MAX_OVERFLOW = 0


def apply_sqlite_pragmas(dbapi_connection: Any) -> None:
    try:
        busy_timeout_ms = int((os.environ.get("SQLITE_BUSY_TIMEOUT_MS") or str(SQLITE_BUSY_TIMEOUT_MS)).strip() or SQLITE_BUSY_TIMEOUT_MS)
    except Exception:
        busy_timeout_ms = int(SQLITE_BUSY_TIMEOUT_MS)
    busy_timeout_ms = max(1000, min(int(busy_timeout_ms), 5 * 60_000))

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.fetchone()
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA temp_store = MEMORY")
        cursor.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    finally:
        cursor.close()


def _is_sqlite_file_url(database_url: str) -> bool:
    try:
        url = make_url(database_url)
    except Exception:
        return False
    if (url.get_backend_name() or "").lower() != "sqlite":
        return False
    db = str(url.database or "").strip()
    return bool(db and db != ":memory:")


def create_engine(database_url: str) -> AsyncEngine:
    kwargs: dict[str, Any] = {}
    if database_url.lower().startswith("sqlite"):
        try:
            busy_timeout_ms = int((os.environ.get("SQLITE_BUSY_TIMEOUT_MS") or str(SQLITE_BUSY_TIMEOUT_MS)).strip() or SQLITE_BUSY_TIMEOUT_MS)
        except Exception:
            busy_timeout_ms = int(SQLITE_BUSY_TIMEOUT_MS)
        busy_timeout_ms = max(1000, min(int(busy_timeout_ms), 5 * 60_000))

        kwargs["connect_args"] = {"timeout": float(busy_timeout_ms) / 1000.0}
        if _is_sqlite_file_url(database_url):
            # 限制单进程内同时打开的 SQLite 连接数，减少并发写导致的 "database is locked"。
            kwargs.update(
                {
                    "pool_size": int(SQLITE_POOL_SIZE),
                    "max_overflow": int(SQLITE_MAX_OVERFLOW),
                }
            )

    engine = create_async_engine(database_url, **kwargs)

    if database_url.lower().startswith("sqlite"):
        def _on_connect(dbapi_connection: Any, _record: Any) -> None:
            apply_sqlite_pragmas(dbapi_connection)

        event.listen(engine.sync_engine, "connect", _on_connect)

    return engine
