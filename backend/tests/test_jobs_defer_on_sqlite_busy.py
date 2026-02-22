from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.db.models.base import Base
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker
from app.jobs.dispatch import JobDispatcher
from app.jobs.executor import execute_claimed_job
from app.main import create_app


def test_execute_job_defers_on_sqlite_busy(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "jobs_defer_busy.db"
    db_url = "sqlite+aiosqlite:///" + db_path.as_posix()

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("SECRET_KEY", "secret_test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")

    app = create_app()
    engine = app.state.engine

    async def _seed() -> dict:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = create_sessionmaker(engine)
        async with Session() as session:
            row = JobRow(
                type="dummy_busy",
                status="running",
                payload_json="{}",
                last_error=None,
                priority=0,
                run_after=None,
                attempt=0,
                max_attempts=3,
                locked_by="w1",
                locked_at="2026-02-21T00:00:00.000Z",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)

            return {
                "id": int(row.id),
                "type": row.type,
                "status": row.status,
                "payload_json": row.payload_json,
                "attempt": int(row.attempt),
                "max_attempts": int(row.max_attempts),
                "run_after": row.run_after,
                "last_error": row.last_error,
                "locked_by": row.locked_by,
                "locked_at": row.locked_at,
            }

    job_row = asyncio.run(_seed())

    dispatcher = JobDispatcher()

    @dispatcher.handler("dummy_busy")
    async def _handler(_job: dict) -> None:
        raise sqlite3.OperationalError("database is locked")

    now = datetime(2026, 2, 21, 0, 0, 0, tzinfo=timezone.utc)

    async def _run() -> None:
        transition = await execute_claimed_job(engine, dispatcher, job_row=job_row, worker_id="w1", now=now)
        assert transition is not None
        assert transition.attempt == 0
        assert transition.status.value == "failed"
        assert transition.run_after is not None
        assert transition.last_error is not None
        assert "database is locked" in transition.last_error.lower()

    asyncio.run(_run())

