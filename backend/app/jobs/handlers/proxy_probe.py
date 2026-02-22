from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import load_settings
from app.core.crypto import FieldEncryptor
from app.core.metrics import PROXY_PROBE_LATENCY_MS
from app.core.redact import redact_text
from app.core.time import iso_utc_ms
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.jobs.errors import JobPermanentError

DEFAULT_PROBE_URL = "https://www.pixiv.net/robots.txt"
DEFAULT_TIMEOUT_MS = 8000
DEFAULT_CONCURRENCY = 10

BLACKLIST_AFTER_FAILURES = 3
BLACKLIST_TTL_S = 30 * 60


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


def _parse_payload(payload_json: str) -> dict[str, Any]:
    try:
        data = json.loads(payload_json)
    except Exception as exc:
        raise JobPermanentError("payload_json is not valid JSON") from exc
    if not isinstance(data, dict):
        raise JobPermanentError("payload_json must be an object")
    return data


def _truncate(text: str, *, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _build_proxy_uri(
    encryptor: FieldEncryptor,
    *,
    scheme: str,
    host: str,
    port: int,
    username: str,
    password_enc: str,
) -> str:
    scheme = (scheme or "").strip().lower()
    host = (host or "").strip()
    username = (username or "").strip()
    password_enc = (password_enc or "").strip()
    if not scheme or not host or int(port) <= 0:
        raise ValueError("invalid proxy endpoint")

    password = encryptor.decrypt_text(password_enc) if password_enc else ""

    host_part = host
    if ":" in host_part and not host_part.startswith("["):
        host_part = f"[{host_part}]"

    auth = ""
    if username:
        user_q = quote(username, safe="")
        pass_q = quote(password or "", safe="")
        auth = f"{user_q}:{pass_q}@"

    return f"{scheme}://{auth}{host_part}:{int(port)}"


async def _default_probe(target: ProbeTarget, cfg: ProbeConfig) -> ProbeResult:
    start = time.monotonic()
    ok = False
    err: str | None = None

    try:
        async with httpx.AsyncClient(
            proxy=target.proxy_uri,
            timeout=httpx.Timeout(cfg.timeout_s, connect=min(10.0, cfg.timeout_s)),
            follow_redirects=True,
        ) as client:
            resp = await client.get(cfg.url)
        ok = int(resp.status_code) < 400
        if not ok:
            err = f"status={resp.status_code}"
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"

    latency_ms = (time.monotonic() - start) * 1000.0
    return ProbeResult(endpoint_id=int(target.endpoint_id), ok=bool(ok), latency_ms=float(latency_ms), error=err)


def build_proxy_probe_handler(
    engine: AsyncEngine,
    *,
    prober: ProbeFunc | None = None,
) -> Any:
    settings = load_settings()
    encryptor = FieldEncryptor.from_key(settings.field_encryption_key)
    Session = create_sessionmaker(engine)

    probe_fn = prober or _default_probe

    async def _handler(job: dict[str, Any]) -> None:
        payload_json = str(job.get("payload_json") or "")
        payload = _parse_payload(payload_json)

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
                proxy_uri = _build_proxy_uri(
                    encryptor,
                    scheme=str(ep.scheme),
                    host=str(ep.host),
                    port=int(ep.port),
                    username=str(ep.username or ""),
                    password_enc=str(ep.password_enc or ""),
                )
            except Exception as exc:
                immediate_results.append(
                    ProbeResult(endpoint_id=int(ep.id), ok=False, latency_ms=None, error=f"{type(exc).__name__}: {exc}")
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
                            .values(
                                last_latency_ms=latency,
                                last_ok_at=now_iso,
                                success_count=ProxyEndpoint.success_count + 1,
                                last_error=None,
                                blacklisted_until=None,
                                updated_at=now_iso,
                            )
                        )
                        continue

                    msg = _truncate(redact_text(r.error or "probe_failed"))
                    blacklist_expr = sa.case(
                        (
                            (ProxyEndpoint.failure_count + 1) >= int(BLACKLIST_AFTER_FAILURES),
                            blacklist_until_iso,
                        ),
                        else_=ProxyEndpoint.blacklisted_until,
                    )
                    await session.execute(
                        sa.update(ProxyEndpoint)
                        .where(ProxyEndpoint.id == int(r.endpoint_id))
                        .values(
                            last_latency_ms=latency,
                            last_fail_at=now_iso,
                            failure_count=ProxyEndpoint.failure_count + 1,
                            blacklisted_until=blacklist_expr,
                            last_error=msg,
                            updated_at=now_iso,
                        )
                    )

                await session.commit()

        await with_sqlite_busy_retry(_op)

    return _handler
