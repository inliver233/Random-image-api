from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.coerce import format_exc, truncate_text
from app.core.config import Settings, load_settings
from app.core.http_client import acquire_proxy_client
from app.core.metrics import PROXY_PROBE_LATENCY_MS
from app.core.proxy_health import (
    PROBE_BLACKLIST_AFTER_FAILURES,
    PROBE_BLACKLIST_TTL_S,
    proxy_endpoint_fail_values_threshold,
    proxy_endpoint_ok_values,
)
from app.core.proxy_routing import _proxy_uri_from_endpoint_row, invalidate_proxy_pool_caches
from app.core.redact import redact_text
from app.core.time import iso_utc_ms
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.jobs.payload import parse_job_payload_object
from app.jobs.errors import JobPermanentError

DEFAULT_PROBE_URL = "https://www.pixiv.net/robots.txt"
DEFAULT_TIMEOUT_MS = 8000
DEFAULT_CONCURRENCY = 10

# Re-export historical names for tests/importers that pin these constants.
BLACKLIST_AFTER_FAILURES = PROBE_BLACKLIST_AFTER_FAILURES
BLACKLIST_TTL_S = PROBE_BLACKLIST_TTL_S


@dataclass(frozen=True, slots=True)
class ProbeConfig:
    url: str
    timeout_s: float


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    endpoint_id: int
    proxy_uri: str


@dataclass(frozen=True, slots=True)
class ProbeResult:
    endpoint_id: int
    ok: bool
    latency_ms: float | None
    error: str | None = None


ProbeFunc = Callable[[ProbeTarget, ProbeConfig], Awaitable[ProbeResult]]


async def _default_probe(target: ProbeTarget, cfg: ProbeConfig) -> ProbeResult:
    start = time.monotonic()
    ok = False
    err: str | None = None

    try:
        # Reuse process-local proxy client pool (same as residential stream/oauth).
        lease = await acquire_proxy_client(target.proxy_uri)
        try:
            resp = await lease.client.get(cfg.url, timeout=float(cfg.timeout_s))
        finally:
            await lease.release()
        ok = int(resp.status_code) < 400
        if not ok:
            err = f"status={resp.status_code}"
    except Exception as exc:
        err = format_exc(exc)

    latency_ms = (time.monotonic() - start) * 1000.0
    return ProbeResult(endpoint_id=int(target.endpoint_id), ok=bool(ok), latency_ms=float(latency_ms), error=err)


def build_proxy_probe_handler(
    engine: AsyncEngine,
    *,
    prober: ProbeFunc | None = None,
    settings: Settings | None = None,
) -> Any:
    if settings is None:
        settings = load_settings()
    Session = create_sessionmaker(engine)

    probe_fn = prober or _default_probe

    async def _handler(job: dict[str, Any]) -> None:
        payload_json = str(job.get("payload_json") or "")
        payload = parse_job_payload_object(payload_json)

        probe_url = str(payload.get("probe_url") or DEFAULT_PROBE_URL).strip() or DEFAULT_PROBE_URL
        timeout_ms_raw = payload.get("timeout_ms", DEFAULT_TIMEOUT_MS)
        concurrency_raw = payload.get("concurrency", DEFAULT_CONCURRENCY)

        try:
            timeout_ms = int(timeout_ms_raw)
        except Exception as exc:
            raise JobPermanentError("payload.timeout_ms invalid") from exc
        if timeout_ms <= 0 or timeout_ms > 600_000:
            raise JobPermanentError("payload.timeout_ms invalid")

        try:
            concurrency = int(concurrency_raw)
        except Exception as exc:
            raise JobPermanentError("payload.concurrency invalid") from exc
        if concurrency < 1:
            concurrency = 1
        if concurrency > 200:
            concurrency = 200

        cfg = ProbeConfig(url=probe_url, timeout_s=float(timeout_ms) / 1000.0)

        async with Session() as session:
            endpoints = (
                (
                    await session.execute(
                        sa.select(ProxyEndpoint).where(ProxyEndpoint.enabled == 1).order_by(ProxyEndpoint.id.asc())
                    )
                )
                .scalars()
                .all()
            )

        if not endpoints:
            return

        targets: list[ProbeTarget] = []
        immediate_results: list[ProbeResult] = []
        for ep in endpoints:
            try:
                # Share Fernet URI cache with residential pick path.
                built = _proxy_uri_from_endpoint_row(
                    settings,
                    endpoint_id=int(ep.id),
                    pool_id=0,
                    scheme=str(ep.scheme),
                    host=str(ep.host),
                    port=int(ep.port),
                    username=str(ep.username or ""),
                    password_enc=str(ep.password_enc or ""),
                )
                proxy_uri = built.uri
            except Exception as exc:
                immediate_results.append(
                    ProbeResult(endpoint_id=int(ep.id), ok=False, latency_ms=None, error=format_exc(exc))
                )
                continue
            targets.append(ProbeTarget(endpoint_id=int(ep.id), proxy_uri=proxy_uri))

        sem = asyncio.Semaphore(int(concurrency))

        async def _run_one(t: ProbeTarget) -> ProbeResult:
            async with sem:
                return await probe_fn(t, cfg)

        tasks = [asyncio.create_task(_run_one(t)) for t in targets]
        probed = await asyncio.gather(*tasks) if tasks else []

        results = list(immediate_results) + list(probed)
        for r in results:
            if r.latency_ms is not None and float(r.latency_ms) >= 0:
                PROXY_PROBE_LATENCY_MS.observe(float(r.latency_ms))
        now_dt = datetime.now(timezone.utc)
        now_iso = iso_utc_ms(now_dt)
        blacklist_until_iso = iso_utc_ms(now_dt + timedelta(seconds=int(BLACKLIST_TTL_S)))

        async def _op() -> None:
            async with Session() as session:
                for r in results:
                    latency = float(r.latency_ms) if r.latency_ms is not None else None
                    if r.ok:
                        await session.execute(
                            sa.update(ProxyEndpoint)
                            .where(ProxyEndpoint.id == int(r.endpoint_id))
                            .values(**proxy_endpoint_ok_values(now_iso=now_iso, latency_ms=latency))
                        )
                        continue

                    msg = truncate_text(redact_text(r.error or "probe_failed"))
                    await session.execute(
                        sa.update(ProxyEndpoint)
                        .where(ProxyEndpoint.id == int(r.endpoint_id))
                        .values(
                            **proxy_endpoint_fail_values_threshold(
                                now_iso=now_iso,
                                latency_ms=latency,
                                error_message=msg,
                                blacklist_until_iso=blacklist_until_iso,
                                after_failures=int(BLACKLIST_AFTER_FAILURES),
                            )
                        )
                    )

                await session.commit()

        await with_sqlite_busy_retry(_op)
        # Blacklist / health changes must drop eligible-endpoint cache immediately.
        invalidate_proxy_pool_caches()

    return _handler
