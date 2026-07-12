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


class _ControllableQueue:
    backend = "fake"

    def __init__(self) -> None:
        self.renew_calls: list[tuple[int, str]] = []
        self._renew_results: list[bool] = []
        self._default_renew = True

    def set_renew_sequence(self, *results: bool) -> None:
        self._renew_results = list(results)

    async def renew_lock(self, *, job_id: int, worker_id: str, now=None) -> bool:  # noqa: ANN001
        _ = now
        self.renew_calls.append((int(job_id), str(worker_id)))
        if self._renew_results:
            return bool(self._renew_results.pop(0))
        return bool(self._default_renew)


async def _seed_running_job(engine, *, job_id: int = 7, worker_id: str = "w-hb") -> None:
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
            f"""
INSERT INTO jobs (id,type,status,priority,run_after,payload_json,locked_by,locked_at,created_at,updated_at,attempt,max_attempts)
VALUES ({int(job_id)},'noop','running',0,NULL,'{{}}','{worker_id}','2026-02-10T00:00:00.000Z','2026-02-10T00:00:00.000Z','2026-02-10T00:00:00.000Z',0,3);
""".strip()
        )


def test_execute_claimed_job_aborts_when_lock_renew_fails(tmp_path: Path, monkeypatch) -> None:
    """renew_lock False (cancel/reclaim) must cancel handler and skip terminal transition."""
    db_path = tmp_path / "executor_lock_lost.db"
    engine = create_engine(_sqlite_url(db_path))
    fake_queue = _ControllableQueue()
    # First renew fails → lock lost while handler still sleeping.
    fake_queue.set_renew_sequence(False)

    monkeypatch.setattr("app.jobs.executor._renew_interval_s", lambda _ttl: 0.05)

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _run() -> None:
        await _seed_running_job(engine)

        async def _slow(_job_row: dict[str, Any]) -> None:
            started.set()
            try:
                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                cancelled.set()
                raise

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
            queue=fake_queue,  # type: ignore[arg-type]
        )
        assert transition is None
        assert started.is_set()
        assert cancelled.is_set()
        assert len(fake_queue.renew_calls) >= 1

        # Row must still be running under original lock (executor did not overwrite).
        async with engine.begin() as conn:
            row = (
                await conn.exec_driver_sql("SELECT status, locked_by FROM jobs WHERE id=7")
            ).mappings().first()
        assert row is not None
        assert row["status"] == "running"
        assert row["locked_by"] == "w-hb"

        await engine.dispose()

    asyncio.run(_run())


def test_execute_claimed_job_success_still_completes_when_lock_held(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "executor_lock_ok.db"
    engine = create_engine(_sqlite_url(db_path))
    fake_queue = _ControllableQueue()
    fake_queue._default_renew = True

    monkeypatch.setattr("app.jobs.executor._renew_interval_s", lambda _ttl: 0.05)

    async def _run() -> None:
        await _seed_running_job(engine)

        async def _quick(_job_row: dict[str, Any]) -> None:
            await asyncio.sleep(0.12)

        dispatcher = JobDispatcher()
        dispatcher.register("noop", _quick)

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
            queue=fake_queue,  # type: ignore[arg-type]
        )
        assert transition is not None
        assert transition.status == JobStatus.COMPLETED
        await engine.dispose()

    asyncio.run(_run())
