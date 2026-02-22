from __future__ import annotations

import asyncio
import os
import signal
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.easy_proxies.auto_refresh import EasyProxiesAutoRefreshConfig, EasyProxiesAutoRefresher
from app.core.config import load_settings
from app.core.logging import configure_logging, get_logger
from app.core.redact import redact_text
from app.core.time import iso_utc_ms
from app.core.runtime_settings import set_runtime_setting
from app.db.engine import create_engine
from app.jobs.claim import DEFAULT_LOCK_TTL_S, claim_next_job
from app.jobs.dispatch import JobDispatcher
from app.jobs.errors import JobPermanentError
from app.jobs.executor import execute_claimed_job
from app.jobs.handlers.easy_proxies_import import build_easy_proxies_import_handler
from app.jobs.handlers.heal_url import build_heal_url_handler
from app.jobs.handlers.hydrate_metadata import build_hydrate_metadata_handler
from app.jobs.handlers.import_images import build_import_images_handler
from app.jobs.handlers.proxy_probe import build_proxy_probe_handler

log = get_logger(__name__)


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    signals = [signal.SIGINT]
    if hasattr(signal, "SIGTERM"):
        signals.append(signal.SIGTERM)

    for sig in signals:
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop_event.set())
        except Exception:
            continue


def _disabled_handler(job_type: str, *, reason: str):
    async def _handler(_job: dict[str, Any]) -> None:
        raise JobPermanentError(f"{job_type} handler disabled: {reason}")

    return _handler


def build_default_dispatcher(engine) -> JobDispatcher:
    dispatcher = JobDispatcher()
    dispatcher.register("import_images", build_import_images_handler(engine))

    def _safe_register(job_type: str, builder: Callable[[], Any]) -> None:
        try:
            dispatcher.register(job_type, builder())
        except Exception as exc:
            msg = redact_text(f"{type(exc).__name__}: {exc}")
            log.warning("jobs_handler_disabled type=%s reason=%s", job_type, msg)
            dispatcher.register(job_type, _disabled_handler(job_type, reason=msg))

    _safe_register("hydrate_metadata", lambda: build_hydrate_metadata_handler(engine))
    _safe_register("heal_url", lambda: build_heal_url_handler(engine))
    _safe_register("proxy_probe", lambda: build_proxy_probe_handler(engine))
    _safe_register("easy_proxies_import", lambda: build_easy_proxies_import_handler(engine))
    return dispatcher


def _parse_int_env(name: str, *, default: int, min_v: int, max_v: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except Exception:
        return int(default)
    return max(int(min_v), min(int(value), int(max_v)))


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip()
    if raw == "":
        return bool(default)
    v = raw.lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def compute_desired_worker_concurrency(
    *,
    auto_enabled: bool,
    enabled_tokens: int | None,
    max_concurrency: int,
) -> int:
    max_c = max(1, int(max_concurrency))
    if not bool(auto_enabled):
        return max_c
    n = int(enabled_tokens or 0)
    if n <= 0:
        n = 1
    return max(1, min(max_c, n))


async def _count_enabled_tokens(engine) -> int | None:
    try:
        async with engine.connect() as conn:
            value = (await conn.exec_driver_sql("SELECT COUNT(*) FROM pixiv_tokens WHERE enabled=1;")).scalar_one()
        return int(value or 0)
    except Exception:
        return None


class _JobScheduler:
    def __init__(self, engine, dispatcher: JobDispatcher, *, worker_id: str, lock_ttl_s: int) -> None:
        self._engine = engine
        self._dispatcher = dispatcher
        self._worker_id = str(worker_id)
        self._lock_ttl_s = int(lock_ttl_s)
        self._tasks: set[asyncio.Task] = set()
        self._stopping = False

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        try:
            _ = task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            msg = redact_text(f"{type(exc).__name__}: {exc}")
            log.warning("job_task_failed err=%s", msg)

    async def tick(self, *, desired_concurrency: int, max_claims: int) -> int:
        if self._stopping:
            return 0

        desired = max(1, int(desired_concurrency))
        slots = max(0, int(desired - len(self._tasks)))
        if slots <= 0:
            return 0

        claimed = 0
        for _ in range(int(min(slots, max(1, int(max_claims))))):
            try:
                job_row = await claim_next_job(
                    self._engine,
                    worker_id=str(self._worker_id),
                    lock_ttl_s=int(self._lock_ttl_s),
                )
            except Exception as exc:
                msg = redact_text(f"{type(exc).__name__}: {exc}")
                log.warning("jobs_claim_failed err=%s", msg)
                break
            if job_row is None:
                break

            async def _run(row: dict[str, Any]) -> None:
                try:
                    await execute_claimed_job(
                        self._engine,
                        self._dispatcher,
                        job_row=row,
                        worker_id=str(self._worker_id),
                    )
                except Exception as exc:
                    msg = redact_text(f"{type(exc).__name__}: {exc}")
                    log.warning("job_execute_failed err=%s", msg)

            task = asyncio.create_task(_run(job_row))
            task.add_done_callback(self._on_task_done)
            self._tasks.add(task)
            claimed += 1

        return int(claimed)

    async def shutdown(self) -> None:
        self._stopping = True
        if not self._tasks:
            return
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        _ = await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


async def poll_and_execute_jobs(
    engine,
    dispatcher: JobDispatcher,
    *,
    worker_id: str,
    lock_ttl_s: int = DEFAULT_LOCK_TTL_S,
    max_jobs: int = 10,
) -> int:
    worker_id = (worker_id or "").strip()
    if not worker_id:
        raise ValueError("worker_id is required")

    ran = 0
    for _ in range(int(max_jobs)):
        try:
            job_row = await claim_next_job(engine, worker_id=worker_id, lock_ttl_s=int(lock_ttl_s))
        except Exception as exc:
            msg = redact_text(f"{type(exc).__name__}: {exc}")
            log.warning("jobs_claim_failed err=%s", msg)
            break
        if job_row is None:
            break

        try:
            await execute_claimed_job(engine, dispatcher, job_row=job_row, worker_id=worker_id)
        except Exception as exc:
            msg = redact_text(f"{type(exc).__name__}: {exc}")
            log.warning("job_execute_failed err=%s", msg)
        ran += 1
    return ran


async def _poll_once() -> None:
    return None


async def run_worker(
    *,
    poll_interval_s: float = 1.0,
    max_iterations: int | None = None,
    on_tick: Callable[[], Awaitable[None]] | None = None,
) -> None:
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    on_tick = on_tick or _poll_once
    iterations = 0

    while not stop_event.is_set():
        await on_tick()

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break

        if poll_interval_s > 0:
            await asyncio.sleep(poll_interval_s)


async def main_async(*, max_iterations: int | None = None, poll_interval_s: float = 1.0) -> None:
    configure_logging()
    settings = load_settings()

    engine = create_engine(settings.database_url)
    scheduler: _JobScheduler | None = None
    try:
        dispatcher = build_default_dispatcher(engine)

        base_url = (os.environ.get("EASY_PROXIES_BASE_URL") or "").strip()
        raw_auto = (os.environ.get("EASY_PROXIES_AUTO_REFRESH") or "").strip().lower()
        auto_refresh_disabled = raw_auto in {"0", "false", "no", "n", "off"}

        raw_ms = (os.environ.get("EASY_PROXIES_REFRESH_INTERVAL_MS") or "").strip()
        raw_s = (os.environ.get("EASY_PROXIES_REFRESH_INTERVAL_SECONDS") or "").strip()
        if not raw_s:
            raw_s = (os.environ.get("EASY_PROXIES_REFRESH_INTERVAL_S") or "").strip()

        interval_s = 0.0
        if raw_ms:
            try:
                interval_s = float(raw_ms) / 1000.0
            except Exception:
                interval_s = 0.0
        elif raw_s:
            try:
                interval_s = float(raw_s)
            except Exception:
                interval_s = 0.0

        if auto_refresh_disabled:
            interval_s = 0.0

        conflict_policy = (os.environ.get("EASY_PROXIES_CONFLICT_POLICY") or "skip_non_easy_proxies").strip()

        host_override = (os.environ.get("EASY_PROXIES_HOST_OVERRIDE") or "").strip() or None

        raw_attach = (os.environ.get("EASY_PROXIES_AUTO_ATTACH") or "").strip().lower()
        auto_attach_disabled = raw_attach in {"0", "false", "no", "n", "off"}

        attach_pool_id: int | None = None
        raw_attach_pool = (os.environ.get("EASY_PROXIES_ATTACH_POOL_ID") or "").strip()
        if raw_attach_pool:
            try:
                attach_pool_id = int(raw_attach_pool)
            except Exception:
                attach_pool_id = None
            if attach_pool_id is not None and int(attach_pool_id) <= 0:
                attach_pool_id = None

        attach_weight = _parse_int_env(
            "EASY_PROXIES_ATTACH_WEIGHT",
            default=1,
            min_v=0,
            max_v=1000,
        )

        raw_recompute = (os.environ.get("EASY_PROXIES_AUTO_RECOMPUTE_BINDINGS") or "").strip().lower()
        recompute_disabled = raw_recompute in {"0", "false", "no", "n", "off"}
        recompute_bindings = not recompute_disabled

        max_tokens_per_proxy = _parse_int_env(
            "EASY_PROXIES_MAX_TOKENS_PER_PROXY",
            default=2,
            min_v=1,
            max_v=1000,
        )

        raw_strict = (os.environ.get("EASY_PROXIES_BINDINGS_STRICT") or "").strip().lower()
        strict = raw_strict in {"1", "true", "yes", "y", "on"}

        refresher = EasyProxiesAutoRefresher(
            EasyProxiesAutoRefreshConfig(
                base_url=base_url,
                interval_s=interval_s,
                conflict_policy=conflict_policy or "skip_non_easy_proxies",
                host_override=host_override,
                auto_attach=not auto_attach_disabled,
                attach_pool_id=attach_pool_id,
                attach_weight=int(attach_weight),
                recompute_bindings=bool(recompute_bindings),
                max_tokens_per_proxy=int(max_tokens_per_proxy),
                strict=bool(strict),
            )
        )
        if refresher.enabled:
            log.info("easy_proxies_auto_refresh_enabled base_url=%s interval_s=%s", base_url, interval_s)

        worker_id = (os.environ.get("WORKER_ID") or f"pid{os.getpid()}").strip()
        jobs_lock_ttl_s = _parse_int_env(
            "WORKER_JOBS_LOCK_TTL_SECONDS",
            default=int(DEFAULT_LOCK_TTL_S),
            min_v=5,
            max_v=3600,
        )
        max_jobs_per_tick = _parse_int_env(
            "WORKER_MAX_JOBS_PER_TICK",
            default=10,
            min_v=1,
            max_v=1000,
        )
        max_concurrency = _parse_int_env(
            "WORKER_MAX_CONCURRENCY",
            default=20,
            min_v=1,
            max_v=200,
        )
        auto_concurrency = _parse_bool_env("WORKER_AUTO_CONCURRENCY", default=True)
        auto_refresh_s = _parse_int_env(
            "WORKER_AUTO_CONCURRENCY_REFRESH_SECONDS",
            default=15,
            min_v=1,
            max_v=3600,
        )
        try:
            heartbeat_interval_s = float((os.environ.get("WORKER_HEARTBEAT_INTERVAL_SECONDS") or "10").strip() or "10")
        except Exception:
            heartbeat_interval_s = 10.0
        heartbeat_interval_s = max(1.0, min(float(heartbeat_interval_s), 300.0))
        last_heartbeat_m = 0.0
        last_auto_refresh_m = 0.0
        cached_enabled_tokens: int | None = None
        cached_desired_concurrency = 1

        scheduler = _JobScheduler(engine, dispatcher, worker_id=str(worker_id), lock_ttl_s=int(jobs_lock_ttl_s))

        async def _on_tick() -> None:
            nonlocal last_heartbeat_m
            nonlocal last_auto_refresh_m
            nonlocal cached_enabled_tokens
            nonlocal cached_desired_concurrency
            now_m = time.monotonic()

            if bool(auto_concurrency) and (now_m - last_auto_refresh_m) >= float(auto_refresh_s):
                last_auto_refresh_m = now_m
                cached_enabled_tokens = await _count_enabled_tokens(engine)

            cached_desired_concurrency = compute_desired_worker_concurrency(
                auto_enabled=bool(auto_concurrency),
                enabled_tokens=cached_enabled_tokens,
                max_concurrency=int(max_concurrency),
            )

            if now_m - last_heartbeat_m >= heartbeat_interval_s:
                last_heartbeat_m = now_m
                try:
                    await set_runtime_setting(
                        engine,
                        key="worker.last_seen_at",
                        value={"at": iso_utc_ms(), "worker_id": worker_id, "pid": int(os.getpid())},
                        description="worker heartbeat",
                        updated_by=f"worker:{worker_id}",
                    )
                    await set_runtime_setting(
                        engine,
                        key="worker.concurrency",
                        value={
                            "at": iso_utc_ms(),
                            "worker_id": worker_id,
                            "auto": bool(auto_concurrency),
                            "enabled_tokens": int(cached_enabled_tokens or 0),
                            "desired": int(cached_desired_concurrency),
                            "max": int(max_concurrency),
                        },
                        description="worker concurrency",
                        updated_by=f"worker:{worker_id}",
                    )
                except Exception:
                    log.warning("worker_heartbeat_update_failed")

            await refresher.tick(engine)

            if scheduler is not None:
                await scheduler.tick(
                    desired_concurrency=int(cached_desired_concurrency),
                    max_claims=int(max_jobs_per_tick),
                )

        log.info("worker_start env=%s", settings.app_env)
        await run_worker(
            max_iterations=max_iterations,
            poll_interval_s=poll_interval_s,
            on_tick=_on_tick,
        )
        log.info("worker_stop")
    finally:
        if scheduler is not None:
            try:
                await scheduler.shutdown()
            except Exception:
                log.warning("worker_scheduler_shutdown_failed")
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    _argv = argv or []
    _ = _argv
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
