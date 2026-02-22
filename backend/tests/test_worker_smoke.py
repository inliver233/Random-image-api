from __future__ import annotations

import asyncio

from app.worker import main_async


def test_worker_smoke_exits_cleanly(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    asyncio.run(main_async(max_iterations=1, poll_interval_s=0.0))

