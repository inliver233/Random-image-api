from __future__ import annotations

import os
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Under concurrent writers (API requests + worker jobs), large imports/backfills can
# legitimately hold the SQLite writer lock for several seconds. A higher default
# busy timeout makes the system much more resilient under load (still overrideable
# via SQLITE_BUSY_TIMEOUT_MS env).
SQLITE_BUSY_TIMEOUT_MS = 30_000
SQLITE_POOL_SIZE = 10
SQLITE_MAX_OVERFLOW = 10
SQLITE_POOL_TIMEOUT_S = 5


def apply_sqlite_pragmas(dbapi_connection: Any) -> None:
    try:
        busy_timeout_ms = int((os.environ.get("SQLITE_BUSY_TIMEOUT_MS") or str(SQLITE_BUSY_TIMEOUT_MS)).strip() or SQLITE_BUSY_TIMEOUT_MS)
    except Exception:
        busy_timeout_ms = int(SQLITE_BUSY_TIMEOUT_MS)
    busy_timeout_ms = max(1000, min(int(busy_timeout_ms), 5 * 60_000))

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        try:
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.fetchone()
        except Exception:
            pass
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
            try:
                pool_size = int((os.environ.get("SQLITE_POOL_SIZE") or str(SQLITE_POOL_SIZE)).strip() or SQLITE_POOL_SIZE)
            except Exception:
                pool_size = int(SQLITE_POOL_SIZE)
            pool_size = max(1, min(int(pool_size), 200))

            try:
                max_overflow = int((os.environ.get("SQLITE_MAX_OVERFLOW") or str(SQLITE_MAX_OVERFLOW)).strip() or SQLITE_MAX_OVERFLOW)
            except Exception:
                max_overflow = int(SQLITE_MAX_OVERFLOW)
            max_overflow = max(0, min(int(max_overflow), 200))

            try:
                pool_timeout_s = float((os.environ.get("SQLITE_POOL_TIMEOUT_S") or str(SQLITE_POOL_TIMEOUT_S)).strip() or SQLITE_POOL_TIMEOUT_S)
            except Exception:
                pool_timeout_s = float(SQLITE_POOL_TIMEOUT_S)
            pool_timeout_s = float(max(0.5, min(float(pool_timeout_s), 120.0)))

            kwargs.update(
                {
                    "pool_size": int(pool_size),
                    "max_overflow": int(max_overflow),
                    "pool_timeout": float(pool_timeout_s),
                }
            )

    engine = create_async_engine(database_url, **kwargs)

    if database_url.lower().startswith("sqlite"):
        def _on_connect(dbapi_connection: Any, _record: Any) -> None:
            apply_sqlite_pragmas(dbapi_connection)

        event.listen(engine.sync_engine, "connect", _on_connect)

    return engine
