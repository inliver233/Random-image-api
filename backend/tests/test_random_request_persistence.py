"""Persisted /random totals read path (H5 call site).

``load_persisted_random_totals`` used raw ``exec_driver_sql`` with the
``%s`` "postgres marker", which asyncpg's numeric_dollar paramstyle can
never execute — and the failure was swallowed into all-zero totals. The
read now goes through ``sa.text`` named binds, which SQLAlchemy compiles
correctly for every installed dialect.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.random_request_persistence import (
    RANDOM_TOTALS_KEY,
    load_persisted_random_totals,
    persist_random_totals,
)
from app.db.engine import create_engine
from app.db.models.base import Base


def test_random_totals_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "random_totals.db"
    engine = create_engine("sqlite+aiosqlite:///" + db_path.as_posix())

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        assert await load_persisted_random_totals(engine) == {
            "total_requests": 0,
            "total_ok": 0,
            "total_error": 0,
        }

        await persist_random_totals(
            engine, total_requests=7, total_ok=5, total_error=2, source="test"
        )

        totals = await load_persisted_random_totals(engine)
        assert totals == {"total_requests": 7, "total_ok": 5, "total_error": 2}

        await engine.dispose()

    asyncio.run(_run())


def test_random_totals_key_is_stable() -> None:
    assert RANDOM_TOTALS_KEY == "stats.random.total"
