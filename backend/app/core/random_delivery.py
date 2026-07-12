from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import BackgroundTasks
from fastapi.responses import RedirectResponse

from app.core.errors import ApiError, ErrorCode
from app.core.http_stream import stream_url
from app.core.image_edge import resolve_image_edge_redirect_url
from app.core.metrics import observe_image_delivery
from app.core.origin_stream import prepare_origin_stream
from app.core.recent_dedup import MemoryRecentDedup, RecentDedupPort
from app.core.random_strategy import needs_opportunistic_hydrate
from app.core.time import iso_utc_ms
from app.db.catalog import CatalogStore, SqliteCatalogStore
from app.jobs.enqueue import enqueue_opportunistic_hydrate_metadata

PickFn = Callable[..., Awaitable[tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]]]


def should_mark_image_ok(image: Any) -> bool:
    """True when last_ok is missing or a prior error is still recorded."""
    return image.last_ok_at is None or image.last_error_code is not None


def resolve_catalog_store(catalog: CatalogStore | None = None) -> CatalogStore:
    """Prefer injected CatalogStore; fall back to default SQLite helpers."""
    return catalog if catalog is not None else SqliteCatalogStore()


def resolve_recent_dedup(dedup: RecentDedupPort | None = None) -> RecentDedupPort:
    """Prefer injected RecentDedupPort; fall back to process-local memory."""
    return dedup if dedup is not None else MemoryRecentDedup()


def resolve_random_service_factory(factory: Any = None) -> Any:
    """Prefer injected RandomServiceFactory; fall back to default plan builder.

    Lazy-import DefaultRandomServiceFactory to avoid circular import with
    random_pick_context → random_engine_pick → random_delivery.
    """
    if factory is not None:
        return factory
    from app.core.random_pick_context import DefaultRandomServiceFactory

    return DefaultRandomServiceFactory()


async def best_effort(fn, *args, timeout_s: float = 1.5, **kwargs) -> None:  # type: ignore[no-untyped-def]
    try:
        await asyncio.wait_for(fn(*args, **kwargs), timeout=float(timeout_s))
    except Exception:
        pass


def attach_background(resp: Any, background_tasks: BackgroundTasks) -> Any:
    try:
        if getattr(resp, "background", None) is None:
            resp.background = background_tasks
    except Exception:
        pass
    return resp


def schedule_mark_ok_if_needed(
    *,
    background_tasks: BackgroundTasks,
    engine: Any,
    image_id: int,
    should_mark_ok: bool,
    now: str | None = None,
    catalog: CatalogStore | None = None,
) -> None:
    """Queue mark_image_ok only when the caller proved delivery and the row needs it."""
    if not should_mark_ok:
        return
    store = resolve_catalog_store(catalog)
    background_tasks.add_task(
        best_effort,
        store.mark_image_ok,
        engine,
        image_id=int(image_id),
        now=now or iso_utc_ms(),
        timeout_s=1.5,
    )


def schedule_hydrate_if_needed(
    *,
    background_tasks: BackgroundTasks,
    engine: Any,
    illust_id: int,
    needs_hydrate: bool,
    hydrate_reason: str,
    queue: Any | None = None,
) -> None:
    """Queue opportunistic hydrate metadata enqueue when the image still needs it."""
    if not needs_hydrate:
        return
    background_tasks.add_task(
        best_effort,
        enqueue_opportunistic_hydrate_metadata,
        engine,
        illust_id=int(illust_id),
        reason=str(hydrate_reason),
        timeout_s=2.5,
        queue=queue,
    )


def schedule_edge_side_effects(
    *,
    background_tasks: BackgroundTasks,
    engine: Any,
    image_id: int,
    illust_id: int,
    user_id: int | None,
    anti_repeat_enabled: bool,
    dedup_window_s: float,
    dedup_max_images: int,
    dedup_max_authors: int,
    needs_hydrate: bool,
    hydrate_reason: str,
    # Edge 302 does not prove bytes were served; do not mark_image_ok (avoids fail_cooldown skew).
    mark_ok_on_edge: bool = False,
    should_mark_ok: bool = False,
    catalog: CatalogStore | None = None,
    recent_dedup: RecentDedupPort | None = None,
    job_queue: Any | None = None,
) -> None:
    if bool(anti_repeat_enabled):
        try:
            resolve_recent_dedup(recent_dedup).record(
                now=time.monotonic(),
                image_id=int(image_id),
                user_id=user_id,
                window_s=float(dedup_window_s),
                max_images=int(dedup_max_images),
                max_authors=int(dedup_max_authors),
            )
        except Exception:
            pass
    if mark_ok_on_edge:
        schedule_mark_ok_if_needed(
            background_tasks=background_tasks,
            engine=engine,
            image_id=image_id,
            should_mark_ok=should_mark_ok,
            catalog=catalog,
        )
    schedule_hydrate_if_needed(
        background_tasks=background_tasks,
        engine=engine,
        illust_id=illust_id,
        needs_hydrate=needs_hydrate,
        hydrate_reason=hydrate_reason,
        queue=job_queue,
    )


def schedule_pick_side_effects(
    *,
    background_tasks: BackgroundTasks,
    engine: Any,
    image: Any,
    pick_ctx: Any,
    hydrate_reason: str,
    mark_ok_on_edge: bool = False,
    should_mark_ok: bool = False,
    catalog: CatalogStore | None = None,
    recent_dedup: RecentDedupPort | None = None,
    job_queue: Any | None = None,
) -> None:
    """Thin wrapper: anti-repeat / hydrate / optional mark_ok from RandomPickContext + image."""
    schedule_edge_side_effects(
        background_tasks=background_tasks,
        engine=engine,
        image_id=int(image.id),
        illust_id=int(image.illust_id),
        user_id=int(image.user_id) if getattr(image, "user_id", None) is not None else None,
        anti_repeat_enabled=bool(pick_ctx.anti_repeat_enabled),
        dedup_window_s=float(pick_ctx.dedup_window_s),
        dedup_max_images=int(pick_ctx.dedup_max_images),
        dedup_max_authors=int(pick_ctx.dedup_max_authors),
        needs_hydrate=needs_opportunistic_hydrate(image),
        hydrate_reason=str(hydrate_reason),
        mark_ok_on_edge=bool(mark_ok_on_edge),
        should_mark_ok=bool(should_mark_ok),
        catalog=catalog,
        recent_dedup=recent_dedup,
        job_queue=job_queue,
    )


def build_edge_redirect_response(
    *,
    edge_url: str,
    cache_control: str = "no-store",
) -> RedirectResponse:
    return RedirectResponse(
        url=edge_url,
        status_code=302,
        headers={"Cache-Control": cache_control, "X-Image-Edge": "1"},
    )


async def deliver_random_image_stream(
    *,
    pick: PickFn,
    Session: Any,
    engine: Any,
    settings: Any,
    runtime: Any,
    httpx_transport: Any,
    httpx_client: Any,
    range_header: str | None,
    attempts: int,
    prefer_edge_redirect: bool = False,
    prefer_edge_stream: bool | None = None,
    use_pixiv_cat: bool,
    mirror_host: str,
    anti_repeat_enabled: bool,
    dedup_window_s: float,
    dedup_max_images: int,
    dedup_max_authors: int,
    background_tasks: BackgroundTasks,
    no_match_error: Callable[[], ApiError],
    catalog: CatalogStore | None = None,
    recent_dedup: RecentDedupPort | None = None,
    job_queue: Any | None = None,
) -> Any:
    """Pick + same-origin edge stream (or local cascade) for /random?format=image.

    H0 product default when image edge is ready: BFF streams signed img-worker URL
    and returns **200 on this domain** (address bar stays on /random; F5 re-picks).
    Browser 302 to workers.dev is **not** this path — callers use redirect=1 +
    ``build_edge_redirect_response`` instead.

    ``prefer_edge_redirect`` is retained for API compatibility but ignored for the
    default stream path (historical name meant "prefer edge when ready").

    After the first pick attempt, subsequent retries pass ``skip_engine=True`` so
    dual-run does not pay another engine RTT when only the origin stream failed
    (mirrors /feed top-up sticky skip).
    """
    # H0: default delivery is same-origin stream via CF edge, not browser 302.
    # prefer_edge_redirect historically meant "edge ready"; map to prefer_edge_stream.
    if prefer_edge_stream is None:
        prefer_edge_stream = bool(prefer_edge_redirect)
    prefer_edge = bool(prefer_edge_stream)
    _ = prefer_edge_redirect  # explicit: do not 302 from format=image default path

    store = resolve_catalog_store(catalog)
    dedup = resolve_recent_dedup(recent_dedup)
    tried_ids: set[int] = set()
    last_error: ApiError | None = None
    attempts_i = max(1, int(attempts))
    # First attempt may dual-run; retries stay on Python pick path.
    skip_engine = False
    # Count edge_unavailable at most once per request (first edge-ready miss).
    edge_unavailable_counted = False

    for _ in range(attempts_i):
        async with Session() as session:
            image, _debug = await pick(
                session=session,
                exclude_image_ids=list(tried_ids),
                skip_engine=skip_engine,
            )
            # Sticky: later attempts never re-enter engine dual-run.
            skip_engine = True
            if image is None:
                break
            image_id = int(image.id)
            origin_url = str(image.original_url)
            illust_id_for_hydrate = int(image.illust_id)
            needs_hydrate = needs_opportunistic_hydrate(image)
            should_mark_ok = should_mark_image_ok(image)
            # Engine DTOs set last_ok_at="engine" so edge/JSON skip catalog mark without a
            # row load. Stream path below proves bytes — force mark_ok for DTOs.
            if getattr(image, "from_engine_item", False):
                should_mark_ok = True
            user_id_for_recent = int(image.user_id) if getattr(image, "user_id", None) is not None else None

        source_url: str | None = None
        proxy_uri: str | None = None
        delivery_via_edge = False

        if prefer_edge:
            try:
                from app.core.image_edge import ensure_image_edge_overlay_fresh

                await ensure_image_edge_overlay_fresh(engine)
            except Exception:
                pass
            edge_url = resolve_image_edge_redirect_url(settings=settings, original_url=origin_url)
            if edge_url:
                # Same-origin 200: BFF pulls signed img-worker (HMAC path → i.pximg.net).
                source_url = edge_url
                proxy_uri = None
                delivery_via_edge = True
            else:
                # Prefer edge but no signed URL → local cascade (count once).
                if not edge_unavailable_counted:
                    observe_image_delivery(path="edge_unavailable")
                    edge_unavailable_counted = True

        if source_url is None:
            source_url, proxy_uri = await prepare_origin_stream(
                engine=engine,
                settings=settings,
                runtime=runtime,
                origin_url=origin_url,
                use_mirror=bool(use_pixiv_cat),
                mirror_host=mirror_host,
            )

        try:
            # Edge stream: no Pixiv Referer (Worker already attaches it to origin).
            stream_kwargs: dict[str, Any] = {
                "transport": httpx_transport,
                "client": httpx_client if not proxy_uri else None,
                "proxy": proxy_uri,
                "cache_control": "no-store",
                "range_header": range_header,
            }
            if delivery_via_edge:
                stream_kwargs["referer"] = ""
            resp = await stream_url(source_url, **stream_kwargs)
            # Stream path proved bytes — mark ok when needed (browser 302 does not).
            schedule_edge_side_effects(
                background_tasks=background_tasks,
                engine=engine,
                image_id=image_id,
                illust_id=illust_id_for_hydrate,
                user_id=user_id_for_recent,
                anti_repeat_enabled=bool(anti_repeat_enabled),
                dedup_window_s=float(dedup_window_s),
                dedup_max_images=int(dedup_max_images),
                dedup_max_authors=int(dedup_max_authors),
                needs_hydrate=bool(needs_hydrate),
                hydrate_reason="random",
                mark_ok_on_edge=True,
                should_mark_ok=bool(should_mark_ok),
                catalog=store,
                recent_dedup=dedup,
                job_queue=job_queue,
            )
            if delivery_via_edge:
                observe_image_delivery(path="edge_stream")
                try:
                    resp.headers["X-Image-Edge"] = "stream"
                except Exception:
                    pass
            else:
                observe_image_delivery(path="local_stream")
                if use_pixiv_cat:
                    observe_image_delivery(path="local_stream_mirror")
                elif proxy_uri:
                    observe_image_delivery(path="local_stream_residential")
                else:
                    observe_image_delivery(path="local_stream_direct")
            return attach_background(resp, background_tasks)
        except ApiError as exc:
            if exc.code in {
                ErrorCode.UPSTREAM_STREAM_ERROR,
                ErrorCode.UPSTREAM_403,
                ErrorCode.UPSTREAM_404,
                ErrorCode.UPSTREAM_RATE_LIMIT,
            }:
                await best_effort(
                    store.mark_image_failure,
                    engine,
                    image_id=image_id,
                    now=iso_utc_ms(),
                    error_code=exc.code.value,
                    error_message=exc.message,
                    timeout_s=1.5,
                )
                tried_ids.add(image_id)
                last_error = exc
                continue
            raise

    if last_error is None:
        raise no_match_error()

    raise ApiError(
        code=ErrorCode.UPSTREAM_STREAM_ERROR,
        message="多次尝试后上游请求仍失败。",
        status_code=502,
        details={
            "attempts_used": len(tried_ids),
            "last_upstream_code": last_error.code.value,
        },
    )
