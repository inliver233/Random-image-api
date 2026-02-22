from __future__ import annotations

import asyncio

import pytest

from app.jobs.dispatch import JobDispatcher


def test_job_dispatch_calls_registered_handler() -> None:
    dispatcher = JobDispatcher()
    called: list[int] = []

    async def _handler(job):
        called.append(int(job["id"]))

    dispatcher.register("noop", _handler)
    asyncio.run(dispatcher.dispatch({"id": 1, "type": "noop"}))
    assert called == [1]


def test_job_dispatch_unknown_type_rejected() -> None:
    dispatcher = JobDispatcher()
    with pytest.raises(ValueError, match="Unknown job type"):
        asyncio.run(dispatcher.dispatch({"id": 1, "type": "unknown"}))


def test_job_dispatch_missing_type_rejected() -> None:
    dispatcher = JobDispatcher()
    with pytest.raises(ValueError, match="Unknown job type"):
        asyncio.run(dispatcher.dispatch({"id": 1}))

