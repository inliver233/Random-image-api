from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.env_parse import parse_float_env, parse_int_env

# Under concurrent writers (API requests + worker jobs), large imports/backfills can
# legitimately hold the SQLite writer lock for several seconds. A higher default
# busy timeout makes the system much more resilient under load (still overrideable
# via SQLITE_BUSY_TIMEOUT_MS env).
#
# NOTE: These defaults favor availability on reasonably powerful servers. For
# smaller instances you may want to lower pool sizes and/or busy timeout.
SQLITE_BUSY_TIMEOUT_MS = 60_000
SQLITE_POOL_SIZE = 30
SQLITE_MAX_OVERFLOW = 30
SQLITE_POOL_TIMEOUT_S = 30


def _sqlite_busy_timeout_ms() -> int:
    return parse_int_env(
        "SQLITE_BUSY_TIMEOUT_MS",
        default=int(SQLITE_BUSY_TIMEOUT_MS),
        min_v=1000,
        max_v=5 * 60_000,
    )


def apply_sqlite_pragmas(dbapi_connection: Any) -> None:
    busy_timeout_ms = _sqlite_busy_timeout_ms()

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
        busy_timeout_ms = _sqlite_busy_timeout_ms()

        kwargs["connect_args"] = {"timeout": float(busy_timeout_ms) / 1000.0}
        if _is_sqlite_file_url(database_url):
            # 限制单进程内同时打开的 SQLite 连接数，减少并发写导致的 "database is locked"。
            pool_size = parse_int_env(
                "SQLITE_POOL_SIZE",
                default=int(SQLITE_POOL_SIZE),
                min_v=1,
                max_v=200,
            )
            max_overflow = parse_int_env(
                "SQLITE_MAX_OVERFLOW",
                default=int(SQLITE_MAX_OVERFLOW),
                min_v=0,
                max_v=200,
            )
            pool_timeout_s = parse_float_env(
                "SQLITE_POOL_TIMEOUT_S",
                default=float(SQLITE_POOL_TIMEOUT_S),
                min_v=0.5,
                max_v=120.0,
            )

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
