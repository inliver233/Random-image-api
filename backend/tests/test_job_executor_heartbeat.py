from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.engine import create_engine
from app.jobs.dispatch import JobDispatcher
from app.jobs.executor import execute_claimed_job
from app.jobs.model import JobStatus


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


def test_execute_claimed_job_renews_lock_while_running(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "executor_hb.db"
    engine = create_engine(_sqlite_url(db_path))
    renew_calls: list[tuple[int, str]] = []

    async def _fake_renew(engine_arg, *, job_id: int, worker_id: str, now=None) -> bool:  # noqa: ANN001
        _ = engine_arg, now
        renew_calls.append((int(job_id), str(worker_id)))
        return True

    monkeypatch.setattr("app.jobs.executor.renew_job_lock", _fake_renew)
    monkeypatch.setattr("app.jobs.executor._renew_interval_s", lambda _ttl: 0.05)

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                """
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT,
  updated_at TEXT,
  type TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  run_after TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  payload_json TEXT NOT NULL,
  last_error TEXT,
  locked_by TEXT,
  locked_at TEXT
);
""".strip()
            )
            await conn.exec_driver_sql(
                """
INSERT INTO jobs (id,type,status,priority,run_after,payload_json,locked_by,locked_at,created_at,updated_at,attempt,max_attempts)
VALUES (7,'noop','running',0,NULL,'{}','w-hb','2026-02-10T00:00:00.000Z','2026-02-10T00:00:00.000Z','2026-02-10T00:00:00.000Z',0,3);
""".strip()
            )

        async def _slow(_job_row: dict[str, Any]) -> None:
            await asyncio.sleep(0.18)

        dispatcher = JobDispatcher()
        dispatcher.register("noop", _slow)

        job_row = {
            "id": 7,
            "type": "noop",
            "status": "running",
            "attempt": 0,
            "max_attempts": 3,
            "run_after": None,
            "last_error": None,
            "locked_by": "w-hb",
            "locked_at": "2026-02-10T00:00:00.000Z",
            "payload_json": "{}",
        }
        now = datetime(2026, 2, 10, 0, 1, 0, tzinfo=timezone.utc)
        transition = await execute_claimed_job(
            engine,
            dispatcher,
            job_row=job_row,
            worker_id="w-hb",
            now=now,
            lock_ttl_s=60,
        )
        assert transition is not None
        assert transition.status == JobStatus.COMPLETED
        assert len(renew_calls) >= 2
        assert all(call == (7, "w-hb") for call in renew_calls)

        await engine.dispose()

    asyncio.run(_run())
