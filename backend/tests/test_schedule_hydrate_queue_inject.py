from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks

from app.core.random_delivery import schedule_hydrate_if_needed
from app.db.engine import create_engine
from app.jobs.enqueue import enqueue_opportunistic_hydrate_metadata


def _sqlite_url(db_path: Path) -> str:
    return "sqlite+aiosqlite:///" + db_path.as_posix()


class _FakeQueue:
    backend = "fake"

    def __init__(self) -> None:
        self.hydrate_calls: list[tuple[int, str]] = []

    async def enqueue_opportunistic_hydrate(self, *, illust_id: int, reason: str) -> int | None:
        self.hydrate_calls.append((int(illust_id), str(reason)))
        return 101


def test_schedule_hydrate_if_needed_skips_when_not_needed() -> None:
    bt = BackgroundTasks()
    fake = _FakeQueue()
    schedule_hydrate_if_needed(
        background_tasks=bt,
        engine=object(),
        illust_id=1,
        needs_hydrate=False,
        hydrate_reason="random",
        queue=fake,
    )
    assert bt.tasks == []
    assert fake.hydrate_calls == []


def test_schedule_hydrate_if_needed_passes_injected_queue(tmp_path: Path) -> None:
    engine = create_engine(_sqlite_url(tmp_path / "hydrate_inject.db"))
    fake = _FakeQueue()
    bt = BackgroundTasks()

    schedule_hydrate_if_needed(
        background_tasks=bt,
        engine=engine,
        illust_id=4242,
        needs_hydrate=True,
        hydrate_reason="image_proxy",
        queue=fake,
    )
    assert len(bt.tasks) == 1

    async def _run() -> None:
        # Starlette stores (func, args, kwargs) on BackgroundTask entries.
        for task in bt.tasks:
            await task()
        await engine.dispose()

    asyncio.run(_run())
    assert fake.hydrate_calls == [(4242, "image_proxy")]


def test_enqueue_opportunistic_hydrate_prefers_injected_queue(tmp_path: Path) -> None:
    engine = create_engine(_sqlite_url(tmp_path / "enqueue_inject.db"))
    fake = _FakeQueue()

    async def _run() -> None:
        job_id = await enqueue_opportunistic_hydrate_metadata(
            engine,
            illust_id=7,
            reason="unit",
            queue=fake,  # type: ignore[arg-type]
        )
        assert job_id == 101
        assert fake.hydrate_calls == [(7, "unit")]
        await engine.dispose()

    asyncio.run(_run())
