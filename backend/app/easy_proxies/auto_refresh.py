from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.logging import get_logger
from app.db.models.jobs import JobRow
from app.db.session import create_sessionmaker, with_sqlite_busy_retry

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EasyProxiesAutoRefreshConfig:
    base_url: str
    interval_s: float
    conflict_policy: str = "skip_non_easy_proxies"
    host_override: str | None = None
    auto_attach: bool = True
    attach_pool_id: int | None = None
    attach_weight: int = 1
    recompute_bindings: bool = True
    max_tokens_per_proxy: int = 2
    strict: bool = False


async def _first_enabled_pool_id(engine: AsyncEngine) -> int | None:
    sql = "SELECT id FROM proxy_pools WHERE enabled=1 ORDER BY id ASC LIMIT 1;"

    async def _op() -> int | None:
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql(sql)
            value = result.scalar_one_or_none()
            return int(value) if value is not None else None

    return await with_sqlite_busy_retry(_op)


async def _enqueue_if_needed(
    engine: AsyncEngine,
    *,
    base_url: str,
    conflict_policy: str,
    payload: dict[str, object],
) -> int | None:
    Session = create_sessionmaker(engine)

    async def _op() -> int | None:
        async with Session() as session:
            existing = await session.execute(
                select(JobRow.id).where(
                    JobRow.type == "easy_proxies_import",
                    JobRow.ref_type == "easy_proxies",
                    JobRow.ref_id == base_url,
                    JobRow.status.in_(("pending", "running")),
                )
            )
            if existing.first() is not None:
                return None

            job = JobRow(
                type="easy_proxies_import",
                status="pending",
                priority=0,
                payload_json=json.dumps(
                    {"base_url": base_url, "conflict_policy": conflict_policy, **payload},
                    ensure_ascii=False,
                ),
                ref_type="easy_proxies",
                ref_id=base_url,
            )
            session.add(job)
            await session.flush()
            await session.commit()
            return int(job.id)

    return await with_sqlite_busy_retry(_op)


class EasyProxiesAutoRefresher:
    def __init__(
        self,
        config: EasyProxiesAutoRefreshConfig,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._now = now or time.time
        self._last_enqueued_at: float | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._config.base_url.strip()) and float(self._config.interval_s) > 0

    async def tick(self, engine: AsyncEngine) -> None:
        if not self.enabled:
            return

        now = float(self._now())
        last = self._last_enqueued_at
        if last is not None and (now - last) < float(self._config.interval_s):
            return

        base_url = self._config.base_url.strip()
        resolved_pool_id = self._config.attach_pool_id
        if bool(self._config.auto_attach) and resolved_pool_id is None:
            resolved_pool_id = await _first_enabled_pool_id(engine)

        payload: dict[str, object] = {}
        if self._config.host_override:
            payload["host_override"] = str(self._config.host_override)
        if resolved_pool_id is not None and int(resolved_pool_id) > 0:
            payload["attach_pool_id"] = int(resolved_pool_id)
            payload["attach_weight"] = int(self._config.attach_weight)
            if bool(self._config.recompute_bindings):
                payload["recompute_bindings"] = True
                payload["max_tokens_per_proxy"] = int(self._config.max_tokens_per_proxy)
                payload["strict"] = bool(self._config.strict)

        job_id = await _enqueue_if_needed(
            engine,
            base_url=base_url,
            conflict_policy=self._config.conflict_policy,
            payload=payload,
        )
        self._last_enqueued_at = now
        if job_id is not None:
            log.info("easy_proxies_auto_refresh_enqueued base_url=%s job_id=%s", base_url, job_id)
