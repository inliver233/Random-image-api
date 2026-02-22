from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class JobDispatcher:
    handlers: dict[str, JobHandler] = field(default_factory=dict)

    def register(self, job_type: str, handler: JobHandler) -> None:
        job_type = job_type.strip()
        if not job_type:
            raise ValueError("job_type is required")
        self.handlers[job_type] = handler

    def handler(self, job_type: str) -> Callable[[JobHandler], JobHandler]:
        def _wrap(fn: JobHandler) -> JobHandler:
            self.register(job_type, fn)
            return fn

        return _wrap

    async def dispatch(self, job: dict[str, Any]) -> None:
        job_type = str(job.get("type") or "").strip()
        handler = self.handlers.get(job_type)
        if handler is None:
            raise ValueError(f"Unknown job type: {job_type or '<missing>'}")
        await handler(job)

