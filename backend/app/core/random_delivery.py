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
from app.core.proxy_routing import select_proxy_uri_for_url
from app.core.pximg_reverse_proxy import rewrite_pximg_to_mirror
from app.core.recent_dedup import record_recent
from app.core.random_strategy import needs_opportunistic_hydrate
from app.core.time import iso_utc_ms
from app.db.images_mark import mark_image_failure, mark_image_ok
from app.jobs.enqueue import enqueue_opportunistic_hydrate_metadata

PickFn = Callable[..., Awaitable[tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]]]


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
) -> None:
    if bool(anti_repeat_enabled):
        try:
            record_recent(
                now=time.monotonic(),
                image_id=int(image_id),
                user_id=user_id,
                window_s=float(dedup_window_s),
                max_images=int(dedup_max_images),
                max_authors=int(dedup_max_authors),
            )
        except Exception:
            pass
    if mark_ok_on_edge and should_mark_ok:
        background_tasks.add_task(
            best_effort, mark_image_ok, engine, image_id=int(image_id), now=iso_utc_ms(), timeout_s=1.5
        )
    if needs_hydrate:
        background_tasks.add_task(
            best_effort,
            enqueue_opportunistic_hydrate_metadata,
            engine,
            illust_id=int(illust_id),
            reason=str(hydrate_reason),
            timeout_s=2.5,
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
    prefer_edge_redirect: bool,
    use_pixiv_cat: bool,
    mirror_host: str,
    anti_repeat_enabled: bool,
    dedup_window_s: float,
    dedup_max_images: int,
    dedup_max_authors: int,
    background_tasks: BackgroundTasks,
    no_match_error: Callable[[], ApiError],
) -> Any:
    """Pick + edge-redirect-or-stream retry loop for /random?format=image."""
    tried_ids: set[int] = set()
    last_error: ApiError | None = None
    attempts_i = max(1, int(attempts))

    for _ in range(attempts_i):
        async with Session() as session:
            image, _debug = await pick(session=session, exclude_image_ids=list(tried_ids))
            if image is None:
                break
            image_id = int(image.id)
            origin_url = str(image.original_url)
            source_url = rewrite_pximg_to_mirror(origin_url, mirror_host=mirror_host) if use_pixiv_cat else origin_url
            illust_id_for_hydrate = int(image.illust_id)
            needs_hydrate = needs_opportunistic_hydrate(image)
            should_mark_ok = image.last_ok_at is None or image.last_error_code is not None
            user_id_for_recent = int(image.user_id) if getattr(image, "user_id", None) is not None else None

        if prefer_edge_redirect:
            edge_url = resolve_image_edge_redirect_url(settings=settings, original_url=origin_url)
            if edge_url:
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
                    mark_ok_on_edge=False,
                    should_mark_ok=bool(should_mark_ok),
                )
                return attach_background(
                    build_edge_redirect_response(edge_url=edge_url, cache_control="no-store"),
                    background_tasks,
                )

        proxy_uri = None
        if not use_pixiv_cat:
            picked = await select_proxy_uri_for_url(engine, settings, runtime, url=origin_url)
            if picked is not None:
                proxy_uri = picked.uri
        try:
            resp = await stream_url(
                source_url,
                transport=httpx_transport,
                client=httpx_client if not proxy_uri else None,
                proxy=proxy_uri,
                cache_control="no-store",
                range_header=range_header,
            )
            # Stream path proved bytes — mark ok when needed (edge 302 does not).
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
            )
            return attach_background(resp, background_tasks)
        except ApiError as exc:
            if exc.code in {
                ErrorCode.UPSTREAM_STREAM_ERROR,
                ErrorCode.UPSTREAM_403,
                ErrorCode.UPSTREAM_404,
                ErrorCode.UPSTREAM_RATE_LIMIT,
            }:
                await best_effort(
                    mark_image_failure,
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
