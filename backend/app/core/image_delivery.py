from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks
from fastapi.responses import RedirectResponse

from app.core.http_stream import stream_url
from app.core.image_edge import resolve_image_edge_redirect_url
from app.core.origin_stream import prepare_origin_stream
from app.core.proxy_mirror import resolve_proxy_mirror
from app.core.random_delivery import (
    attach_background,
    best_effort,
    build_edge_redirect_response,
    should_mark_image_ok,
)
from app.core.random_request import prefer_image_edge
from app.core.random_strategy import needs_opportunistic_hydrate
from app.core.time import iso_utc_ms
from app.db.images_mark import mark_image_failure, mark_image_ok
from app.jobs.enqueue import enqueue_opportunistic_hydrate_metadata

# Re-export for callers that import mark-ok helper from image_delivery.
__all__ = (
    "deliver_known_image",
    "needs_image_proxy_hydrate",
    "should_mark_image_ok",
)


async def needs_image_proxy_hydrate(session: Any, image: Any) -> bool:
    """DB-aware hydrate check for /i proxy (includes missing tags)."""
    if needs_opportunistic_hydrate(image):
        return True
    import sqlalchemy as sa

    from app.db.models.image_tags import ImageTag

    tag_row = (
        await session.execute(sa.select(ImageTag.image_id).where(ImageTag.image_id == int(image.id)).limit(1))
    ).scalar_one_or_none()
    return tag_row is None


async def deliver_known_image(
    *,
    request: Any,
    engine: Any,
    settings: Any,
    runtime: Any,
    image: Any,
    background_tasks: BackgroundTasks | None,
    proxy_override: str | None,
    pixiv_cat: int,
    pximg_mirror_host_override: str | None,
    force_local: bool,
    use_pixiv_cat: bool,
    needs_hydrate: bool = False,
    should_mark_ok: bool = False,
    hydrate_reason: str = "image_proxy",
    cache_control_edge: str = "public, max-age=300",
    cache_control_stream: str = "public, max-age=31536000, immutable",
    mark_fail_on_upstream: bool = False,
) -> Any:
    """Shared edge-prefer + local stream path for /i and legacy routes."""
    if prefer_image_edge(
        proxy_override=proxy_override,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host_override=pximg_mirror_host_override,
        force_local=force_local,
    ) and not use_pixiv_cat:
        edge_url = resolve_image_edge_redirect_url(
            settings=settings,
            original_url=str(image.original_url),
        )
        if edge_url:
            if needs_hydrate and background_tasks is not None:
                background_tasks.add_task(
                    best_effort,
                    enqueue_opportunistic_hydrate_metadata,
                    engine,
                    illust_id=int(image.illust_id),
                    reason=str(hydrate_reason),
                    timeout_s=2.5,
                )
            resp = build_edge_redirect_response(edge_url=edge_url, cache_control=cache_control_edge)
            if background_tasks is not None:
                return attach_background(resp, background_tasks)
            return resp

    resolved = resolve_proxy_mirror(
        runtime=runtime,
        headers=request.headers,
        pixiv_cat=1 if use_pixiv_cat else int(pixiv_cat),
        pximg_mirror_host=pximg_mirror_host_override or proxy_override,
        proxy=proxy_override,
        raise_on_invalid=False,
    )
    # Caller already decided use_pixiv_cat; keep it authoritative for stream source.
    mirror_host = proxy_override or pximg_mirror_host_override or resolved.mirror_host
    use_mirror = bool(use_pixiv_cat) or bool(proxy_override)
    source_url, proxy_uri = await prepare_origin_stream(
        engine=engine,
        settings=settings,
        runtime=runtime,
        origin_url=str(image.original_url),
        use_mirror=use_mirror,
        mirror_host=mirror_host,
    )

    transport = getattr(request.app.state, "httpx_transport", None)
    shared_client = getattr(request.app.state, "httpx_client", None)
    now = iso_utc_ms()
    try:
        resp = await stream_url(
            source_url,
            transport=transport,
            client=shared_client if not proxy_uri else None,
            proxy=proxy_uri,
            cache_control=cache_control_stream,
            range_header=request.headers.get("Range"),
        )
        if background_tasks is not None:
            if should_mark_ok:
                background_tasks.add_task(
                    best_effort, mark_image_ok, engine, image_id=int(image.id), now=now, timeout_s=1.5
                )
            if needs_hydrate:
                background_tasks.add_task(
                    best_effort,
                    enqueue_opportunistic_hydrate_metadata,
                    engine,
                    illust_id=int(image.illust_id),
                    reason=str(hydrate_reason),
                    timeout_s=2.5,
                )
            return attach_background(resp, background_tasks)
        return resp
    except Exception as exc:
        if mark_fail_on_upstream:
            from app.core.errors import ApiError, ErrorCode

            if isinstance(exc, ApiError) and exc.code in {
                ErrorCode.UPSTREAM_STREAM_ERROR,
                ErrorCode.UPSTREAM_403,
                ErrorCode.UPSTREAM_404,
                ErrorCode.UPSTREAM_RATE_LIMIT,
            }:
                await best_effort(
                    mark_image_failure,
                    engine,
                    image_id=int(image.id),
                    now=now,
                    error_code=exc.code.value,
                    error_message=exc.message,
                    timeout_s=1.5,
                )
        raise
