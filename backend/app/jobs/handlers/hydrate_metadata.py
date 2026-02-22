from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import load_settings
from app.core.crypto import FieldEncryptor, mask_secret
from app.core.errors import ApiError, ErrorCode
from app.core.failover import classify_pixiv_rate_limit, pixiv_rate_limit_backoff_seconds
from app.core.metrics import TOKEN_REFRESH_FAIL_TOTAL
from app.core.proxy_routing import select_proxy_uri_for_url
from app.core.redact import redact_text
from app.core.runtime_settings import RuntimeConfig, load_runtime_config
from app.core.time import iso_utc_ms
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.hydration_runs import HydrationRun
from app.db.models.pixiv_tokens import PixivToken
from app.db.models.proxy_endpoints import ProxyEndpoint
from app.db.models.tags import Tag
from app.db.models.token_proxy_bindings import TokenProxyBinding
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.jobs.errors import JobDeferError, JobPermanentError
from app.pixiv.access_token_cache import AccessTokenCache
from app.pixiv.oauth import OAUTH_TOKEN_PATH, PixivOauthConfig, PixivOauthError, refresh_access_token
from app.pixiv.refresh_backoff import refresh_backoff_seconds
from app.pixiv.token_strategy import NoTokenAvailable, TokenCandidate, choose_token

PIXIV_APP_API_BASE_URL = "https://app-api.pixiv.net"
PIXIV_ILLUST_DETAIL_URL = PIXIV_APP_API_BASE_URL + "/v1/illust/detail"

_MAX_TAGS = 200


@dataclass(frozen=True, slots=True)
class _IllustPage:
    page_index: int
    original_url: str
    ext: str


class TokenDisabledError(RuntimeError):
    pass


def _parse_payload(payload_json: str) -> dict[str, Any]:
    try:
        data = json.loads(payload_json)
    except Exception as exc:
        raise JobPermanentError("payload_json is not valid JSON") from exc
    if not isinstance(data, dict):
        raise JobPermanentError("payload_json must be an object")
    return data


def _parse_iso_utc_to_epoch(value: str, *, now_epoch: float) -> float:
    raw = (value or "").strip()
    if not raw:
        return 0.0
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return now_epoch + 24 * 3600
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return float(dt.astimezone(timezone.utc).timestamp())


def _normalize_iso_utc_seconds(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _derive_orientation(width: int | None, height: int | None) -> tuple[float | None, int | None]:
    if width is None or height is None or width <= 0 or height <= 0:
        return None, None
    if width > height:
        orientation = 2
    elif height > width:
        orientation = 1
    else:
        orientation = 3
    return float(width) / float(height), orientation


def _extract_tags(illust: dict[str, Any]) -> list[tuple[str, str | None]]:
    raw_tags = illust.get("tags")
    if not isinstance(raw_tags, list):
        return []
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for raw in raw_tags[: _MAX_TAGS * 2]:
        if not isinstance(raw, dict):
            continue
        name = _as_str(raw.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        translated = _as_str(raw.get("translated_name"))
        out.append((name, translated))
        if len(out) >= _MAX_TAGS:
            break
    return out


def _extract_original_urls(illust: dict[str, Any], *, page_count: int) -> list[str]:
    urls: list[str] = []
    if page_count <= 1:
        meta_single = illust.get("meta_single_page")
        if isinstance(meta_single, dict):
            url = _as_str(meta_single.get("original_image_url"))
            if url:
                return [url]
        meta_pages = illust.get("meta_pages")
        if isinstance(meta_pages, list) and meta_pages:
            first = meta_pages[0]
            if isinstance(first, dict):
                image_urls = first.get("image_urls")
                if isinstance(image_urls, dict):
                    url = _as_str(image_urls.get("original"))
                    if url:
                        return [url]
        raise ValueError("missing original_image_url")

    meta_pages = illust.get("meta_pages")
    if not isinstance(meta_pages, list) or not meta_pages:
        raise ValueError("missing meta_pages")

    for idx in range(page_count):
        page = meta_pages[idx] if idx < len(meta_pages) else None
        if not isinstance(page, dict):
            raise ValueError("invalid meta_pages")
        image_urls = page.get("image_urls")
        if not isinstance(image_urls, dict):
            raise ValueError("invalid meta_pages.image_urls")
        url = _as_str(image_urls.get("original"))
        if not url:
            raise ValueError("missing meta_pages.image_urls.original")
        urls.append(url)
    return urls


def _parse_pximg_ext(url: str) -> str:
    from app.core.pixiv_urls import parse_pixiv_original_url

    parsed = parse_pixiv_original_url(url)
    return str(parsed.ext)


def _parse_source_import_id(job: dict[str, Any]) -> int | None:
    if str(job.get("ref_type") or "").strip() != "import":
        return None
    ref_id = str(job.get("ref_id") or "").strip()
    if not ref_id:
        return None
    if ":" in ref_id:
        prefix, _rest = ref_id.split(":", 1)
    else:
        prefix = ref_id
    try:
        import_id = int(prefix)
    except Exception:
        return None
    return import_id if import_id > 0 else None


def build_hydrate_metadata_handler(
    engine: AsyncEngine,
    *,
    transport: httpx.BaseTransport | None = None,
    token_strategy: str = "least_error",
) -> Any:
    settings = load_settings()
    encryptor = FieldEncryptor.from_key(settings.field_encryption_key)
    oauth_config = PixivOauthConfig(
        client_id=settings.pixiv_oauth_client_id,
        client_secret=settings.pixiv_oauth_client_secret,
        hash_secret=(settings.pixiv_oauth_hash_secret or "").strip() or None,
    )

    Session = create_sessionmaker(engine)

    token_cache = AccessTokenCache()
    choose_lock = asyncio.Lock()
    last_token_id: int | None = None
    pixiv_throttle_global_lock = asyncio.Lock()
    last_pixiv_request_m_global: float = 0.0
    pixiv_throttle_locks_guard = asyncio.Lock()
    pixiv_throttle_locks_by_token: dict[int, asyncio.Lock] = {}
    last_pixiv_request_m_by_token: dict[int, float] = {}

    def _env_int(name: str, *, default: int, min_v: int, max_v: int) -> int:
        raw = (os.environ.get(name) or "").strip()
        try:
            value = int(raw)
        except Exception:
            value = int(default)
        return max(int(min_v), min(int(value), int(max_v)))

    proxy_blacklist_ttl_s = _env_int(
        "HYDRATE_PROXY_BLACKLIST_TTL_S",
        default=5 * 60,
        min_v=0,
        max_v=24 * 60 * 60,
    )
    proxy_override_ttl_s = _env_int(
        "HYDRATE_PROXY_OVERRIDE_TTL_S",
        default=30 * 60,
        min_v=0,
        max_v=7 * 24 * 60 * 60,
    )
    proxy_failover_attempts = _env_int(
        "HYDRATE_PROXY_FAILOVER_ATTEMPTS",
        default=4,
        min_v=0,
        max_v=50,
    )
    recoverable_defer_base_s = _env_int(
        "HYDRATE_RECOVERABLE_DEFER_BASE_S",
        default=20,
        min_v=1,
        max_v=24 * 60 * 60,
    )
    recoverable_defer_jitter_s = _env_int(
        "HYDRATE_RECOVERABLE_DEFER_JITTER_S",
        default=20,
        min_v=0,
        max_v=24 * 60 * 60,
    )

    def _truncate(text: str, *, max_len: int = 500) -> str:
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    async def _mark_proxy_ok(endpoint_id: int, *, latency_ms: float | None, now_iso: str) -> None:
        if int(endpoint_id) <= 0:
            return

        async def _op() -> None:
            async with Session() as session:
                await session.execute(
                    sa.update(ProxyEndpoint)
                    .where(ProxyEndpoint.id == int(endpoint_id))
                    .values(
                        last_latency_ms=float(latency_ms) if latency_ms is not None else None,
                        last_ok_at=now_iso,
                        success_count=ProxyEndpoint.success_count + 1,
                        last_error=None,
                        blacklisted_until=None,
                        updated_at=now_iso,
                    )
                )
                await session.commit()

        await with_sqlite_busy_retry(_op)

    async def _mark_proxy_fail(
        endpoint_id: int,
        *,
        latency_ms: float | None,
        now_dt: datetime,
        error: BaseException | str,
    ) -> None:
        if int(endpoint_id) <= 0:
            return

        now_iso = iso_utc_ms(now_dt)
        blacklist_until_iso = (
            iso_utc_ms(now_dt + timedelta(seconds=int(proxy_blacklist_ttl_s)))
            if int(proxy_blacklist_ttl_s) > 0
            else None
        )
        msg_raw = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
        msg = _truncate(redact_text(msg_raw))

        async def _op() -> None:
            async with Session() as session:
                if blacklist_until_iso:
                    blacklist_expr = sa.case(
                        (
                            sa.and_(
                                ProxyEndpoint.blacklisted_until.isnot(None),
                                ProxyEndpoint.blacklisted_until > blacklist_until_iso,
                            ),
                            ProxyEndpoint.blacklisted_until,
                        ),
                        else_=blacklist_until_iso,
                    )
                else:
                    blacklist_expr = ProxyEndpoint.blacklisted_until

                await session.execute(
                    sa.update(ProxyEndpoint)
                    .where(ProxyEndpoint.id == int(endpoint_id))
                    .values(
                        last_latency_ms=float(latency_ms) if latency_ms is not None else None,
                        last_fail_at=now_iso,
                        failure_count=ProxyEndpoint.failure_count + 1,
                        blacklisted_until=blacklist_expr,
                        last_error=msg,
                        updated_at=now_iso,
                    )
                )
                await session.commit()

        await with_sqlite_busy_retry(_op)

    async def _set_token_proxy_override(
        *,
        token_id: int,
        pool_id: int,
        endpoint_id: int,
        now_dt: datetime,
    ) -> None:
        if int(proxy_override_ttl_s) <= 0:
            return
        if int(token_id) <= 0 or int(pool_id) <= 0 or int(endpoint_id) <= 0:
            return

        now_iso = iso_utc_ms(now_dt)
        expires_at = iso_utc_ms(now_dt + timedelta(seconds=int(proxy_override_ttl_s)))

        async def _op() -> None:
            async with Session() as session:
                await session.execute(
                    sa.update(TokenProxyBinding)
                    .where(
                        sa.and_(
                            TokenProxyBinding.token_id == int(token_id),
                            TokenProxyBinding.pool_id == int(pool_id),
                        )
                    )
                    .values(
                        override_proxy_id=int(endpoint_id),
                        override_expires_at=expires_at,
                        updated_at=now_iso,
                    )
                )
                await session.commit()

        await with_sqlite_busy_retry(_op)

    def _is_recoverable_exc(exc: BaseException) -> bool:
        if isinstance(exc, httpx.RequestError):
            return True
        if isinstance(exc, ApiError) and exc.code in {
            ErrorCode.PROXY_REQUIRED,
            ErrorCode.PROXY_CONNECT_FAILED,
            ErrorCode.PROXY_AUTH_FAILED,
        }:
            return True
        if isinstance(exc, PixivOauthError):
            status = exc.status_code
            return status is None or int(status) >= 500
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = int(getattr(getattr(exc, "response", None), "status_code", 0) or 0)
            return status_code >= 500
        return False

    def _recoverable_defer_run_after_iso() -> str:
        base = float(recoverable_defer_base_s)
        jitter = float(max(0, int(recoverable_defer_jitter_s)))
        delay_s = base + random.random() * jitter
        retry_dt = datetime.now(timezone.utc) + timedelta(seconds=float(delay_s))
        return iso_utc_ms(retry_dt)

    def _defer_run_after_for_exc(exc: BaseException, *, now_epoch: float) -> str:
        if isinstance(exc, ApiError) and exc.code == ErrorCode.PROXY_REQUIRED:
            details = exc.details or {}
            next_available_at_raw = details.get("next_available_at")
            if isinstance(next_available_at_raw, str):
                next_available_at = next_available_at_raw.strip()
                epoch = _parse_iso_utc_to_epoch(next_available_at, now_epoch=now_epoch)
                if epoch is not None and float(epoch) > float(now_epoch):
                    return next_available_at
        return _recoverable_defer_run_after_iso()

    def _rate_limit_int(
        runtime: RuntimeConfig,
        key: str,
        *,
        default: int,
        min_v: int,
        max_v: int,
    ) -> int:
        raw = (runtime.rate_limit or {}).get(key)
        try:
            value = int(raw)
        except Exception:
            return int(default)
        return max(int(min_v), min(int(value), int(max_v)))

    async def _get_pixiv_token_lock(token_id: int) -> asyncio.Lock:
        lock = pixiv_throttle_locks_by_token.get(int(token_id))
        if lock is not None:
            return lock
        async with pixiv_throttle_locks_guard:
            lock2 = pixiv_throttle_locks_by_token.get(int(token_id))
            if lock2 is not None:
                return lock2
            created = asyncio.Lock()
            pixiv_throttle_locks_by_token[int(token_id)] = created
            return created

    async def _pixiv_throttle(runtime: RuntimeConfig, *, token_id: int | None) -> None:
        nonlocal last_pixiv_request_m_global

        default_min_ms = 800 if transport is None else 0
        default_jitter_ms = 200 if transport is None else 0

        min_interval_ms = _rate_limit_int(
            runtime,
            "pixiv_hydrate_min_interval_ms",
            default=int(default_min_ms),
            min_v=0,
            max_v=60_000,
        )
        jitter_ms = _rate_limit_int(
            runtime,
            "pixiv_hydrate_jitter_ms",
            default=int(default_jitter_ms),
            min_v=0,
            max_v=60_000,
        )
        if min_interval_ms <= 0 and jitter_ms <= 0:
            return

        interval_s = (float(min_interval_ms) + random.random() * float(max(0, jitter_ms))) / 1000.0

        token_id_i = int(token_id or 0)
        if token_id_i > 0:
            lock = await _get_pixiv_token_lock(int(token_id_i))
            async with lock:
                now_m = float(time.monotonic())
                last_m = float(last_pixiv_request_m_by_token.get(int(token_id_i), 0.0))
                wait_s = (last_m + float(interval_s)) - now_m
                if wait_s > 0:
                    await asyncio.sleep(float(wait_s))
                last_pixiv_request_m_by_token[int(token_id_i)] = float(time.monotonic())
            return

        async with pixiv_throttle_global_lock:
            now_m = float(time.monotonic())
            wait_s = (float(last_pixiv_request_m_global) + float(interval_s)) - now_m
            if wait_s > 0:
                await asyncio.sleep(float(wait_s))
            last_pixiv_request_m_global = float(time.monotonic())

    def _as_int(value: Any, *, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _missing_set_from_criteria(criteria: dict[str, Any]) -> set[str]:
        default = {"tags", "geometry", "r18", "ai", "illust_type", "user", "title", "created_at", "popularity"}
        raw = criteria.get("missing")
        if not isinstance(raw, list):
            return set(default)
        out: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                continue
            v = item.strip().lower()
            if not v:
                continue
            if v in {"all", "*"}:
                return set(default)
            out.add(v)
        return out or set(default)

    def _build_missing_predicate_sql(missing: set[str]) -> str:
        parts: list[str] = []

        if "geometry" in missing:
            parts.append("(width IS NULL OR height IS NULL OR orientation IS NULL OR aspect_ratio IS NULL)")
        if "r18" in missing:
            parts.append("(x_restrict IS NULL)")
        if "ai" in missing:
            parts.append("(ai_type IS NULL)")
        if "illust_type" in missing:
            parts.append("(illust_type IS NULL)")
        if "user" in missing:
            parts.append("(user_id IS NULL)")
        if "title" in missing:
            parts.append("(title IS NULL)")
        if "created_at" in missing:
            parts.append("(created_at_pixiv IS NULL)")
        if "tags" in missing:
            parts.append("NOT EXISTS (SELECT 1 FROM image_tags it WHERE it.image_id = images.id)")
        if "popularity" in missing:
            parts.append("(bookmark_count IS NULL OR view_count IS NULL OR comment_count IS NULL)")

        return "(" + " OR ".join(parts) + ")" if parts else "(1=1)"

    async def _load_run_state(run_id: int) -> dict[str, Any]:
        Session = create_sessionmaker(engine)

        async def _op() -> dict[str, Any]:
            async with Session() as session:
                run = await session.get(HydrationRun, int(run_id))
                if run is None:
                    raise JobPermanentError("Hydration run not found")

                criteria: dict[str, Any] = {}
                try:
                    criteria_raw = json.loads(run.criteria_json or "{}")
                    if isinstance(criteria_raw, dict):
                        criteria = dict(criteria_raw)
                except Exception:
                    criteria = {}

                cursor_image_id = 0
                try:
                    cursor_raw = json.loads(run.cursor_json or "{}")
                    if isinstance(cursor_raw, dict):
                        cursor_image_id = _as_int(cursor_raw.get("cursor_image_id"), default=0)
                except Exception:
                    cursor_image_id = 0

                return {
                    "status": str(run.status),
                    "criteria": criteria,
                    "cursor_image_id": int(max(0, cursor_image_id)),
                    "processed": int(run.processed or 0),
                    "success": int(run.success or 0),
                    "failed": int(run.failed or 0),
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                }

        return await with_sqlite_busy_retry(_op)

    async def _mark_run_running(run_id: int) -> None:
        now_iso = iso_utc_ms()
        Session = create_sessionmaker(engine)

        async def _op() -> None:
            async with Session() as session:
                run = await session.get(HydrationRun, int(run_id))
                if run is None:
                    raise JobPermanentError("Hydration run not found")
                if str(run.status) not in {"pending", "running"}:
                    return
                run.status = "running"
                if run.started_at is None:
                    run.started_at = now_iso
                run.updated_at = now_iso
                run.last_error = None
                await session.commit()

        await with_sqlite_busy_retry(_op)

    async def _update_run_progress(
        run_id: int,
        *,
        cursor_image_id: int,
        cursor_illust_id: int,
        processed_inc: int,
        success_inc: int,
        failed_inc: int,
        last_error: str | None,
    ) -> None:
        now_iso = iso_utc_ms()
        cursor_json = json.dumps(
            {"cursor_image_id": int(cursor_image_id), "cursor_illust_id": int(cursor_illust_id)},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        Session = create_sessionmaker(engine)

        async def _op() -> None:
            async with Session() as session:
                run = await session.get(HydrationRun, int(run_id))
                if run is None:
                    raise JobPermanentError("Hydration run not found")
                run.cursor_json = cursor_json
                run.processed = int(run.processed or 0) + int(processed_inc)
                run.success = int(run.success or 0) + int(success_inc)
                run.failed = int(run.failed or 0) + int(failed_inc)
                run.updated_at = now_iso
                if last_error is not None:
                    run.last_error = (str(last_error) or "")[:500]
                await session.commit()

        await with_sqlite_busy_retry(_op)

    async def _mark_run_completed(run_id: int) -> None:
        now_iso = iso_utc_ms()
        Session = create_sessionmaker(engine)

        async def _op() -> None:
            async with Session() as session:
                run = await session.get(HydrationRun, int(run_id))
                if run is None:
                    raise JobPermanentError("Hydration run not found")
                if str(run.status) not in {"pending", "running"}:
                    return
                run.status = "completed"
                if run.finished_at is None:
                    run.finished_at = now_iso
                run.updated_at = now_iso
                run.last_error = None
                await session.commit()

        await with_sqlite_busy_retry(_op)

    async def _release_job_lock(
        *,
        job_id: int,
        worker_id: str,
        status: str,
        run_after: str | None,
    ) -> bool:
        sql = """
UPDATE jobs
SET status=:status,
    run_after=:run_after,
    last_error=NULL,
    locked_by=NULL,
    locked_at=NULL,
    updated_at=:now
WHERE id=:id AND status='running' AND locked_by=:worker_id;
""".strip()

        async def _op() -> bool:
            async with engine.begin() as conn:
                result = await conn.exec_driver_sql(
                    sql,
                    {
                        "status": str(status),
                        "run_after": run_after,
                        "now": iso_utc_ms(),
                        "id": int(job_id),
                        "worker_id": worker_id,
                    },
                )
                return (result.rowcount or 0) == 1

        return await with_sqlite_busy_retry(_op)

    async def _pick_next_candidate(
        *,
        cursor_image_id: int,
        missing_predicate_sql: str,
    ) -> tuple[int, int] | None:
        sql = f"""
SELECT id, illust_id
FROM images
WHERE status=1
  AND id > :cursor
  AND {missing_predicate_sql}
ORDER BY id ASC
LIMIT 1;
""".strip()

        async def _op() -> tuple[int, int] | None:
            async with engine.connect() as conn:
                result = await conn.exec_driver_sql(sql, {"cursor": int(cursor_image_id)})
                row = result.first()
                if row is None:
                    return None
                return int(row[0]), int(row[1])

        return await with_sqlite_busy_retry(_op)

    async def _load_tokens(now_epoch: float) -> list[TokenCandidate]:
        async with Session() as session:
            rows = (
                (await session.execute(sa.select(PixivToken).order_by(PixivToken.id.asc())))
                .scalars()
                .all()
            )
        out: list[TokenCandidate] = []
        for row in rows:
            out.append(
                TokenCandidate(
                    id=int(row.id),
                    enabled=bool(row.enabled),
                    weight=float(row.weight or 0.0),
                    error_count=int(row.error_count or 0),
                    backoff_until=_parse_iso_utc_to_epoch(str(row.backoff_until or ""), now_epoch=now_epoch),
                )
            )
        return out

    async def _choose_token_id(*, now_epoch: float, exclude_ids: set[int]) -> int:
        nonlocal last_token_id
        tokens = await _load_tokens(now_epoch)
        tokens2 = [t for t in tokens if int(t.id) not in exclude_ids]
        try:
            async with choose_lock:
                chosen, new_last = choose_token(
                    tokens2,
                    strategy=token_strategy,
                    now=now_epoch,
                    last_id=last_token_id,
                    r=random.random(),
                )
                last_token_id = int(new_last)
                return int(chosen.id)
        except NoTokenAvailable as exc:
            retry_at = exc.next_retry_at
            if retry_at is None:
                retry_at = now_epoch + 60.0
            retry_dt = datetime.fromtimestamp(float(retry_at), tz=timezone.utc)
            raise JobDeferError(
                f"{ErrorCode.NO_TOKEN_AVAILABLE.value}: No eligible token available",
                run_after=iso_utc_ms(retry_dt),
            ) from exc

    async def _get_refresh_token(token_id: int) -> str:
        async with Session() as session:
            row = await session.get(PixivToken, int(token_id))
            if row is None:
                raise JobPermanentError("Token not found")
            if not bool(row.enabled):
                raise TokenDisabledError("Token disabled")
            return encryptor.decrypt_text(str(row.refresh_token_enc))

    async def _is_token_enabled(token_id: int) -> bool:
        async with Session() as session:
            row = await session.get(PixivToken, int(token_id))
            if row is None:
                raise JobPermanentError("Token not found")
            return bool(row.enabled)

    async def _mark_token_backoff(
        token_id: int,
        *,
        now_dt: datetime,
        attempt: int,
        backoff_s: int,
        code: str,
        message: str,
        rotated_refresh_token: str | None = None,
    ) -> str | None:
        now_iso = iso_utc_ms(now_dt)
        backoff_until = iso_utc_ms(now_dt + timedelta(seconds=int(backoff_s))) if backoff_s > 0 else None
        msg = (message or "")[:500]

        async def _op() -> None:
            async with Session() as session:
                row = await session.get(PixivToken, int(token_id))
                if row is None:
                    return
                row.error_count = int(attempt)
                row.backoff_until = backoff_until
                row.last_fail_at = now_iso
                row.last_error_code = str(code)
                row.last_error_msg = msg
                row.updated_at = now_iso
                if rotated_refresh_token:
                    row.refresh_token_enc = encryptor.encrypt_text(rotated_refresh_token)
                    row.refresh_token_masked = mask_secret(rotated_refresh_token)
                await session.commit()

        await with_sqlite_busy_retry(_op)
        return backoff_until

    async def _rotate_refresh_token(token_id: int, *, rotated_refresh_token: str, now_dt: datetime) -> None:
        rotated_refresh_token = (rotated_refresh_token or "").strip()
        if not rotated_refresh_token:
            return

        now_iso = iso_utc_ms(now_dt)

        async def _op() -> None:
            async with Session() as session:
                row = await session.get(PixivToken, int(token_id))
                if row is None:
                    return
                row.refresh_token_enc = encryptor.encrypt_text(rotated_refresh_token)
                row.refresh_token_masked = mask_secret(rotated_refresh_token)
                row.updated_at = now_iso
                await session.commit()

        await with_sqlite_busy_retry(_op)

    async def _mark_token_ok(token_id: int, *, now_dt: datetime) -> None:
        now_iso = iso_utc_ms(now_dt)

        async def _op() -> None:
            async with Session() as session:
                row = await session.get(PixivToken, int(token_id))
                if row is None:
                    return
                row.error_count = 0
                row.backoff_until = None
                row.last_ok_at = now_iso
                row.last_fail_at = None
                row.last_error_code = None
                row.last_error_msg = None
                row.updated_at = now_iso
                await session.commit()

        await with_sqlite_busy_retry(_op)

    async def _get_access_token(token_id: int, *, now_dt: datetime, runtime: RuntimeConfig) -> str:
        if not await _is_token_enabled(int(token_id)):
            raise TokenDisabledError("Token disabled")

        oauth_url = oauth_config.base_url.rstrip("/") + OAUTH_TOKEN_PATH

        async def refresher() -> Any:
            if not await _is_token_enabled(int(token_id)):
                raise TokenDisabledError("Token disabled")
            refresh_token = await _get_refresh_token(token_id)

            last_exc: BaseException | None = None
            max_tries = max(1, int(proxy_failover_attempts) + 1)

            for _try in range(max_tries):
                attempt_now_dt = datetime.now(timezone.utc)
                attempt_now_iso = iso_utc_ms(attempt_now_dt)

                proxy_uri = None
                picked_proxy = await select_proxy_uri_for_url(
                    engine,
                    settings,
                    runtime,
                    url=oauth_url,
                    token_id=int(token_id),
                )
                if picked_proxy is not None:
                    proxy_uri = picked_proxy.uri

                start_m = float(time.monotonic())
                try:
                    await _pixiv_throttle(runtime, token_id=int(token_id))
                    token = await refresh_access_token(
                        refresh_token=refresh_token,
                        config=oauth_config,
                        transport=transport,
                        proxy=proxy_uri,
                    )
                except PixivOauthError as exc:
                    latency_ms = (float(time.monotonic()) - start_m) * 1000.0
                    if picked_proxy is not None:
                        if exc.status_code is not None and int(exc.status_code) < 500:
                            await _mark_proxy_ok(
                                int(picked_proxy.endpoint_id),
                                latency_ms=float(latency_ms),
                                now_iso=str(attempt_now_iso),
                            )
                        else:
                            await _mark_proxy_fail(
                                int(picked_proxy.endpoint_id),
                                latency_ms=float(latency_ms),
                                now_dt=attempt_now_dt,
                                error=exc,
                            )

                    if exc.status_code is None or int(exc.status_code) >= 500:
                        last_exc = exc
                        if picked_proxy is None:
                            break
                        continue
                    raise
                except httpx.RequestError as exc:
                    latency_ms = (float(time.monotonic()) - start_m) * 1000.0
                    if picked_proxy is not None:
                        await _mark_proxy_fail(
                            int(picked_proxy.endpoint_id),
                            latency_ms=float(latency_ms),
                            now_dt=attempt_now_dt,
                            error=exc,
                        )
                    last_exc = exc
                    if picked_proxy is None:
                        break
                    continue
                else:
                    latency_ms = (float(time.monotonic()) - start_m) * 1000.0
                    if picked_proxy is not None:
                        await _mark_proxy_ok(
                            int(picked_proxy.endpoint_id),
                            latency_ms=float(latency_ms),
                            now_iso=str(attempt_now_iso),
                        )
                        await _set_token_proxy_override(
                            token_id=int(token_id),
                            pool_id=int(picked_proxy.pool_id),
                            endpoint_id=int(picked_proxy.endpoint_id),
                            now_dt=attempt_now_dt,
                        )

                    rotated = token.refresh_token
                    if rotated:
                        await _rotate_refresh_token(token_id, rotated_refresh_token=rotated, now_dt=attempt_now_dt)
                    return token

            if last_exc is not None:
                raise last_exc
            raise PixivOauthError("OAuth refresh failed", status_code=None)

        token = await token_cache.get_or_refresh(token_id, refresher=refresher)
        if not await _is_token_enabled(int(token_id)):
            token_cache.invalidate(token_id)
            raise TokenDisabledError("Token disabled")
        return str(token.access_token)

    async def _fetch_illust_detail(
        *,
        illust_id: int,
        access_token: str,
        token_id: int,
        runtime: RuntimeConfig,
        now_dt: datetime,
    ) -> dict[str, Any]:
        headers = oauth_config.build_headers(client_time=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        headers["Authorization"] = f"Bearer {access_token}"

        last_exc: BaseException | None = None
        max_tries = max(1, int(proxy_failover_attempts) + 1)

        for _try in range(max_tries):
            attempt_now_dt = datetime.now(timezone.utc)
            attempt_now_iso = iso_utc_ms(attempt_now_dt)

            proxy_uri = None
            picked_proxy = await select_proxy_uri_for_url(
                engine,
                settings,
                runtime,
                url=PIXIV_ILLUST_DETAIL_URL,
                token_id=int(token_id),
            )
            if picked_proxy is not None:
                proxy_uri = picked_proxy.uri

            client_kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(30.0, connect=10.0),
                "follow_redirects": True,
            }
            if transport is not None:
                client_kwargs["transport"] = transport
            if proxy_uri:
                client_kwargs["proxy"] = proxy_uri

            start_m = float(time.monotonic())
            try:
                await _pixiv_throttle(runtime, token_id=int(token_id))
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.get(
                        PIXIV_ILLUST_DETAIL_URL,
                        params={"illust_id": int(illust_id), "filter": "for_android"},
                        headers=headers,
                    )
            except httpx.RequestError as exc:
                latency_ms = (float(time.monotonic()) - start_m) * 1000.0
                if picked_proxy is not None:
                    await _mark_proxy_fail(
                        int(picked_proxy.endpoint_id),
                        latency_ms=float(latency_ms),
                        now_dt=attempt_now_dt,
                        error=exc,
                    )
                last_exc = exc
                if picked_proxy is None:
                    break
                continue

            latency_ms = (float(time.monotonic()) - start_m) * 1000.0
            if picked_proxy is not None:
                if int(resp.status_code) >= 500:
                    await _mark_proxy_fail(
                        int(picked_proxy.endpoint_id),
                        latency_ms=float(latency_ms),
                        now_dt=attempt_now_dt,
                        error=f"status={int(resp.status_code)}",
                    )
                else:
                    await _mark_proxy_ok(
                        int(picked_proxy.endpoint_id),
                        latency_ms=float(latency_ms),
                        now_iso=str(attempt_now_iso),
                    )

            if resp.status_code == 200:
                if picked_proxy is not None:
                    await _set_token_proxy_override(
                        token_id=int(token_id),
                        pool_id=int(picked_proxy.pool_id),
                        endpoint_id=int(picked_proxy.endpoint_id),
                        now_dt=attempt_now_dt,
                    )

                try:
                    data = resp.json()
                except Exception as exc:
                    raise ValueError("Pixiv App API response is not JSON") from exc
                if not isinstance(data, dict):
                    raise ValueError("Pixiv App API response invalid")
                return data

            http_exc = httpx.HTTPStatusError(
                f"Pixiv App API error status={resp.status_code}",
                request=resp.request,
                response=resp,
            )
            if int(resp.status_code) >= 500:
                last_exc = http_exc
                if picked_proxy is None:
                    break
                continue
            raise http_exc

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Pixiv App API request failed")

    async def _persist(
        *,
        illust_id: int,
        pages: list[_IllustPage],
        width: int | None,
        height: int | None,
        aspect_ratio: float | None,
        orientation: int | None,
        x_restrict: int | None,
        ai_type: int | None,
        illust_type: int | None,
        user_id: int | None,
        user_name: str | None,
        title: str | None,
        created_at_pixiv: str | None,
        bookmark_count: int | None,
        view_count: int | None,
        comment_count: int | None,
        tags: list[tuple[str, str | None]],
        source_import_id: int | None,
    ) -> None:
        now_expr = sa.text("(strftime('%Y-%m-%dT%H:%M:%fZ','now'))")
        normalized_tag_names = [name for name, _t in tags]

        async def _op() -> None:
            async with Session() as session:
                existing = {}
                if normalized_tag_names:
                    rows = (
                        (
                            await session.execute(
                                sa.select(Tag).where(Tag.name.in_(normalized_tag_names))
                            )
                        )
                        .scalars()
                        .all()
                    )
                    existing = {str(t.name): t for t in rows}

                tag_ids: dict[str, int] = {}
                for name, translated in tags:
                    row = existing.get(name)
                    if row is None:
                        row = Tag(name=name, translated_name=translated)
                        session.add(row)
                        await session.flush()
                        existing[name] = row
                    else:
                        if translated is not None and translated != row.translated_name:
                            row.translated_name = translated
                            row.updated_at = iso_utc_ms()
                    tag_ids[name] = int(row.id)

                image_ids: list[int] = []
                for page in pages:
                    stmt = sqlite_insert(Image).values(
                        illust_id=int(illust_id),
                        page_index=int(page.page_index),
                        ext=str(page.ext),
                        original_url=str(page.original_url),
                        proxy_path="",
                        random_key=random.random(),
                        width=width,
                        height=height,
                        aspect_ratio=aspect_ratio,
                        orientation=orientation,
                        x_restrict=x_restrict,
                        ai_type=ai_type,
                        illust_type=illust_type,
                        user_id=user_id,
                        user_name=user_name,
                        title=title,
                        created_at_pixiv=created_at_pixiv,
                        bookmark_count=bookmark_count,
                        view_count=view_count,
                        comment_count=comment_count,
                        created_import_id=int(source_import_id) if source_import_id else None,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["illust_id", "page_index"],
                        set_={
                            "ext": stmt.excluded.ext,
                            "original_url": stmt.excluded.original_url,
                            "width": stmt.excluded.width,
                            "height": stmt.excluded.height,
                            "aspect_ratio": stmt.excluded.aspect_ratio,
                            "orientation": stmt.excluded.orientation,
                            "x_restrict": stmt.excluded.x_restrict,
                            "ai_type": stmt.excluded.ai_type,
                            "illust_type": stmt.excluded.illust_type,
                            "user_id": stmt.excluded.user_id,
                            "user_name": stmt.excluded.user_name,
                            "title": stmt.excluded.title,
                            "created_at_pixiv": stmt.excluded.created_at_pixiv,
                            "bookmark_count": stmt.excluded.bookmark_count,
                            "view_count": stmt.excluded.view_count,
                            "comment_count": stmt.excluded.comment_count,
                            "updated_at": now_expr,
                        },
                    ).returning(Image.id)

                    result = await session.execute(stmt)
                    image_id = int(result.scalar_one())
                    image_ids.append(image_id)

                    proxy_path = f"/i/{image_id}.{page.ext}"
                    await session.execute(
                        sa.update(Image).where(Image.id == image_id).values(proxy_path=proxy_path)
                    )

                if image_ids:
                    await session.execute(sa.delete(ImageTag).where(ImageTag.image_id.in_(image_ids)))
                    if tag_ids:
                        values = [
                            {"image_id": int(img_id), "tag_id": int(tag_id)}
                            for img_id in image_ids
                            for tag_id in tag_ids.values()
                        ]
                        stmt2 = sqlite_insert(ImageTag).values(values).on_conflict_do_nothing()
                        await session.execute(stmt2)

                await session.commit()

        await with_sqlite_busy_retry(_op)

    async def _hydrate_single_illust(*, illust_id: int, source_import_id: int | None) -> None:
        now_dt = datetime.now(timezone.utc)
        now_epoch = float(time.time())
        runtime = await load_runtime_config(engine)

        tried: set[int] = set()
        last_exc: BaseException | None = None

        for _ in range(0, 10):
            try:
                token_id = await _choose_token_id(now_epoch=now_epoch, exclude_ids=tried)
            except JobDeferError as exc:
                if last_exc is not None and _is_recoverable_exc(last_exc):
                    code = ErrorCode.PROXY_CONNECT_FAILED
                    if isinstance(last_exc, ApiError):
                        code = last_exc.code
                    raise JobDeferError(
                        f"{code.value}: 代理/网络异常，稍后重试",
                        run_after=_defer_run_after_for_exc(last_exc, now_epoch=now_epoch),
                    ) from last_exc
                raise
            tried.add(int(token_id))

            try:
                access_token = await _get_access_token(token_id, now_dt=now_dt, runtime=runtime)
            except TokenDisabledError as exc:
                last_exc = exc
                continue
            except ApiError as exc:
                last_exc = exc
                continue
            except httpx.RequestError as exc:
                TOKEN_REFRESH_FAIL_TOTAL.inc()
                last_exc = exc
                continue
            except PixivOauthError as exc:
                TOKEN_REFRESH_FAIL_TOTAL.inc()
                if exc.status_code is None or int(exc.status_code) >= 500:
                    last_exc = exc
                    continue
                attempt = 0
                async with Session() as session:
                    row = await session.get(PixivToken, int(token_id))
                    if row is not None:
                        attempt = int(row.error_count or 0) + 1
                backoff_s = refresh_backoff_seconds(attempt=attempt, status_code=exc.status_code)
                await _mark_token_backoff(
                    token_id,
                    now_dt=now_dt,
                    attempt=attempt,
                    backoff_s=backoff_s,
                    code=ErrorCode.TOKEN_REFRESH_FAILED.value,
                    message="Token refresh failed",
                )
                last_exc = exc
                continue
            except Exception as exc:
                TOKEN_REFRESH_FAIL_TOTAL.inc()
                attempt = 0
                async with Session() as session:
                    row = await session.get(PixivToken, int(token_id))
                    if row is not None:
                        attempt = int(row.error_count or 0) + 1
                backoff_s = refresh_backoff_seconds(attempt=attempt, status_code=None)
                await _mark_token_backoff(
                    token_id,
                    now_dt=now_dt,
                    attempt=attempt,
                    backoff_s=backoff_s,
                    code=ErrorCode.TOKEN_REFRESH_FAILED.value,
                    message="Token refresh failed",
                )
                last_exc = exc
                continue

            try:
                data = await _fetch_illust_detail(
                    illust_id=illust_id,
                    access_token=access_token,
                    token_id=int(token_id),
                    runtime=runtime,
                    now_dt=now_dt,
                )
            except httpx.HTTPStatusError as exc:
                status = int(getattr(exc.response, "status_code", 0) or 0)
                body_text = getattr(exc.response, "text", None)

                if status == 404:
                    raise JobPermanentError("Pixiv illust not found") from exc

                rate_kind = classify_pixiv_rate_limit(status_code=status, body_text=body_text)
                if rate_kind is not None:
                    attempt = 0
                    async with Session() as session:
                        row = await session.get(PixivToken, int(token_id))
                        if row is not None:
                            attempt = int(row.error_count or 0) + 1
                    backoff_s = pixiv_rate_limit_backoff_seconds(attempt=attempt)
                    backoff_until = await _mark_token_backoff(
                        token_id,
                        now_dt=now_dt,
                        attempt=attempt,
                        backoff_s=backoff_s,
                        code=ErrorCode.TOKEN_BACKOFF.value,
                        message="Pixiv rate limited",
                    )
                    if backoff_until:
                        last_exc = JobDeferError("Pixiv rate limited", run_after=backoff_until)
                    continue

                last_exc = exc
                continue
            except ApiError as exc:
                last_exc = exc
                continue
            except httpx.RequestError as exc:
                last_exc = exc
                continue
            except Exception as exc:
                last_exc = exc
                continue

            illust = data.get("illust") if isinstance(data, dict) else None
            if not isinstance(illust, dict):
                raise JobPermanentError("Pixiv illust detail missing illust")

            page_count = _as_int(illust.get("page_count")) or 1
            if page_count <= 0 or page_count > 1000:
                raise JobPermanentError("Pixiv illust detail invalid page_count")

            try:
                urls = _extract_original_urls(illust, page_count=int(page_count))
            except Exception as exc:
                raise JobPermanentError("Pixiv illust detail missing original urls") from exc

            pages: list[_IllustPage] = []
            for idx, url in enumerate(urls):
                url_s = str(url)
                try:
                    ext = _parse_pximg_ext(url_s)
                except Exception as exc:
                    bad = redact_text(url_s)
                    if len(bad) > 300:
                        bad = bad[:297] + "..."
                    raise JobPermanentError(f"Pixiv illust detail invalid original url: {bad}") from exc
                pages.append(_IllustPage(page_index=int(idx), original_url=url_s, ext=ext))

            width = _as_int(illust.get("width"))
            height = _as_int(illust.get("height"))
            aspect_ratio, orientation = _derive_orientation(width, height)

            x_restrict = _as_int(illust.get("x_restrict"))
            ai_type = _as_int(illust.get("illust_ai_type"))
            if ai_type is None:
                ai_type = _as_int(illust.get("ai_type"))

            illust_type = _as_int(illust.get("illust_type"))
            if illust_type is None:
                kind = _as_str(illust.get("type"))
                if kind == "illust":
                    illust_type = 0
                elif kind == "manga":
                    illust_type = 1
                elif kind == "ugoira":
                    illust_type = 2
            if illust_type is not None and int(illust_type) not in {0, 1, 2}:
                illust_type = None

            user = illust.get("user")
            user_id = _as_int(user.get("id")) if isinstance(user, dict) else None
            user_name = _as_str(user.get("name")) if isinstance(user, dict) else None

            title = _as_str(illust.get("title"))
            created_at_pixiv = None
            try:
                created_at_pixiv = _normalize_iso_utc_seconds(_as_str(illust.get("create_date")))
            except Exception:
                created_at_pixiv = None

            bookmark_count = _as_int(illust.get("total_bookmarks"))
            if bookmark_count is None:
                bookmark_count = _as_int(illust.get("bookmark_count"))

            view_count = _as_int(illust.get("total_view"))
            if view_count is None:
                view_count = _as_int(illust.get("view_count"))

            comment_count = _as_int(illust.get("total_comments"))
            if comment_count is None:
                comment_count = _as_int(illust.get("comment_count"))

            tags = _extract_tags(illust)

            await _persist(
                illust_id=int(illust_id),
                pages=pages,
                width=width,
                height=height,
                aspect_ratio=aspect_ratio,
                orientation=orientation,
                x_restrict=x_restrict,
                ai_type=ai_type,
                illust_type=illust_type,
                user_id=user_id,
                user_name=user_name,
                title=title,
                created_at_pixiv=created_at_pixiv,
                bookmark_count=bookmark_count,
                view_count=view_count,
                comment_count=comment_count,
                tags=tags,
                source_import_id=source_import_id,
            )
            await _mark_token_ok(token_id, now_dt=now_dt)
            return

        if isinstance(last_exc, JobDeferError):
            raise last_exc
        if last_exc is None:
            raise RuntimeError("hydrate_metadata failed")
        if _is_recoverable_exc(last_exc):
            code = ErrorCode.PROXY_CONNECT_FAILED
            if isinstance(last_exc, ApiError):
                code = last_exc.code
            raise JobDeferError(
                f"{code.value}: 代理/网络异常，稍后重试",
                run_after=_defer_run_after_for_exc(last_exc, now_epoch=now_epoch),
            ) from last_exc
        raise last_exc

    async def _handler(job: dict[str, Any]) -> None:
        payload_json = str(job.get("payload_json") or "")
        payload = _parse_payload(payload_json)

        hydration_run_id = _as_int(payload.get("hydration_run_id"), default=0)
        if hydration_run_id > 0:
            job_id = _as_int(job.get("id"), default=0)
            worker_id = str(job.get("locked_by") or "").strip()
            if job_id <= 0 or not worker_id:
                raise JobPermanentError("Invalid job state")

            run_state = await _load_run_state(int(hydration_run_id))
            status = str(run_state.get("status") or "")
            if status in {"paused", "canceled"}:
                await _release_job_lock(job_id=int(job_id), worker_id=worker_id, status=status, run_after=None)
                return
            if status not in {"pending", "running"}:
                await _release_job_lock(job_id=int(job_id), worker_id=worker_id, status="completed", run_after=None)
                return

            await _mark_run_running(int(hydration_run_id))

            criteria = run_state.get("criteria") if isinstance(run_state.get("criteria"), dict) else {}
            missing = _missing_set_from_criteria(criteria)
            missing_predicate_sql = _build_missing_predicate_sql(missing)

            cursor_image_id = int(run_state.get("cursor_image_id") or 0)
            batch_size = _as_int(os.environ.get("HYDRATION_RUN_BATCH_SIZE"), default=10)
            batch_size = max(1, min(int(batch_size), 200))

            processed = 0
            for _ in range(batch_size):
                candidate = await _pick_next_candidate(
                    cursor_image_id=int(cursor_image_id),
                    missing_predicate_sql=missing_predicate_sql,
                )
                if candidate is None:
                    await _mark_run_completed(int(hydration_run_id))
                    return

                image_id, illust_id = candidate

                try:
                    await _hydrate_single_illust(illust_id=int(illust_id), source_import_id=None)
                except JobDeferError:
                    raise
                except Exception as exc:
                    await _update_run_progress(
                        int(hydration_run_id),
                        cursor_image_id=int(image_id),
                        cursor_illust_id=int(illust_id),
                        processed_inc=1,
                        success_inc=0,
                        failed_inc=1,
                        last_error=f"{type(exc).__name__}: {exc}",
                    )
                    cursor_image_id = int(image_id)
                    processed += 1
                    continue

                await _update_run_progress(
                    int(hydration_run_id),
                    cursor_image_id=int(image_id),
                    cursor_illust_id=int(illust_id),
                    processed_inc=1,
                    success_inc=1,
                    failed_inc=0,
                    last_error=None,
                )
                cursor_image_id = int(image_id)
                processed += 1

            if processed > 0:
                run_after = iso_utc_ms(datetime.now(timezone.utc) + timedelta(seconds=1))
                await _release_job_lock(job_id=int(job_id), worker_id=worker_id, status="pending", run_after=run_after)
            return

        try:
            illust_id = int(payload.get("illust_id"))
        except Exception as exc:
            raise JobPermanentError("payload.illust_id is required") from exc
        if illust_id <= 0:
            raise JobPermanentError("payload.illust_id is required")

        source_import_id = _parse_source_import_id(job)
        await _hydrate_single_illust(illust_id=int(illust_id), source_import_id=source_import_id)

    return _handler
