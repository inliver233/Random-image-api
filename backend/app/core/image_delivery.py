from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks

from app.core.errors import ApiError, ErrorCode
from app.core.http_stream import stream_url
from app.core.image_edge import (
    ensure_image_edge_overlay_fresh,
    image_edge_is_ready,
    resolve_image_edge_redirect_url,
)
from app.core.metrics import observe_image_delivery
from app.core.origin_stream import prepare_origin_stream
from app.core.pixiv_urls import ALLOWED_IMAGE_EXTS
from app.core.proxy_mirror import resolve_proxy_mirror
from app.core.random_delivery import (
    attach_background,
    best_effort,
    build_edge_redirect_response,
    resolve_catalog_store,
    schedule_hydrate_if_needed,
    schedule_mark_ok_if_needed,
    should_mark_image_ok,
)
from app.core.random_request import (
    force_local_from_query,
    prefer_edge_browser_redirect_from_query,
    prefer_image_edge,
)
from app.core.random_strategy import needs_opportunistic_hydrate
from app.core.runtime_config_cache import resolve_runtime_for_request
from app.core.time import iso_utc_ms
from app.db.catalog import CatalogStore

# Re-export for callers that import mark-ok helper from image_delivery.
__all__ = (
    "deliver_known_image",
    "deliver_public_image_from_request",
    "needs_image_proxy_hydrate",
    "normalize_image_ext",
    "should_mark_image_ok",
)


def normalize_image_ext(ext: str | None) -> str:
    """Lowercase image ext and reject values outside the public allowlist."""
    normalized = (ext or "").lower()
    if normalized not in ALLOWED_IMAGE_EXTS:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported ext", status_code=400)
    return normalized


async def needs_image_proxy_hydrate(session: Any, image: Any, *, tag_store: Any | None = None) -> bool:
    """DB-aware hydrate check for /i proxy (includes missing tags)."""
    if needs_opportunistic_hydrate(image):
        return True
    from app.db.tag_store import resolve_tag_store

    tags = resolve_tag_store(tag_store)
    return not await tags.image_has_any_tag(session, image_id=int(image.id))


async def deliver_public_image_from_request(
    *,
    request: Any,
    image: Any,
    pixiv_cat: int = 0,
    pximg_mirror_host: str | None = None,
    proxy: str | None = None,
    background_tasks: BackgroundTasks | None = None,
    needs_hydrate: bool = False,
    should_mark_ok: bool = False,
    hydrate_reason: str = "image_proxy",
    mark_fail_on_upstream: bool = False,
) -> Any:
    """Resolve runtime + proxy/mirror query flags, then deliver a known image row."""
    engine = request.app.state.engine
    catalog = resolve_catalog_store(getattr(request.app.state, "catalog_store", None))
    job_queue = getattr(request.app.state, "job_queue", None)
    runtime = await resolve_runtime_for_request(request, engine)
    resolved = resolve_proxy_mirror(
        runtime=runtime,
        headers=request.headers,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host=pximg_mirror_host,
        proxy=proxy,
    )
    force_local = force_local_from_query(request.query_params)
    # H0: /i default = same-origin stream via CF; only redirect=1 / edge_redirect=1 → browser 302.
    prefer_browser_302 = prefer_edge_browser_redirect_from_query(request.query_params)
    return await deliver_known_image(
        request=request,
        engine=engine,
        settings=request.app.state.settings,
        runtime=runtime,
        image=image,
        background_tasks=background_tasks,
        proxy_override=resolved.proxy_override,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host_override=resolved.pximg_mirror_host_override,
        force_local=force_local,
        use_pixiv_cat=resolved.use_pixiv_cat,
        needs_hydrate=bool(needs_hydrate),
        should_mark_ok=bool(should_mark_ok),
        hydrate_reason=str(hydrate_reason),
        mark_fail_on_upstream=bool(mark_fail_on_upstream),
        catalog=catalog,
        job_queue=job_queue,
        prefer_edge_browser_redirect=prefer_browser_302,
    )


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
    catalog: CatalogStore | None = None,
    job_queue: Any | None = None,
    # H0: same-origin 200 via CF edge stream by default; opt-in browser 302 only.
    prefer_edge_browser_redirect: bool = False,
) -> Any:
    """Shared edge-prefer + local stream path for /i and legacy routes.

    H0 product default when image edge is ready: BFF streams signed img-worker URL
    and returns **200 on this domain**. Browser 302 to workers.dev only when
    ``prefer_edge_browser_redirect=True`` (e.g. ``?redirect=1`` / ``edge_redirect=1``).
    ``?local=1`` and explicit mirror/proxy query still skip edge.
    """
    store = resolve_catalog_store(catalog)
    # Multi-process: refresh runtime image pool members before readiness/sign.
    await ensure_image_edge_overlay_fresh(engine)
    # Gate on edge ready: default-off must not count edge_unavailable on every /i.
    # Runtime image_proxy_use_pixiv_cat must NOT demote edge — only explicit query
    # overrides (proxy= / pixiv_cat=1 / pximg_mirror_host= / local=1) skip edge.
    # Runtime mirror default applies only on local cascade after edge miss.
    prefer_edge = (
        prefer_image_edge(
            proxy_override=proxy_override,
            pixiv_cat=int(pixiv_cat),
            pximg_mirror_host_override=pximg_mirror_host_override,
            force_local=force_local,
        )
        and image_edge_is_ready(settings)
    )
    edge_url: str | None = None
    if prefer_edge:
        edge_url = resolve_image_edge_redirect_url(
            settings=settings,
            original_url=str(image.original_url),
        )
        if edge_url and prefer_edge_browser_redirect:
            # Opt-in browser 302 — does not prove bytes; never mark_image_ok here.
            if background_tasks is not None:
                schedule_hydrate_if_needed(
                    background_tasks=background_tasks,
                    engine=engine,
                    illust_id=int(image.illust_id),
                    needs_hydrate=bool(needs_hydrate),
                    hydrate_reason=str(hydrate_reason),
                    queue=job_queue,
                )
            observe_image_delivery(path="edge_redirect")
            resp = build_edge_redirect_response(edge_url=edge_url, cache_control=cache_control_edge)
            if background_tasks is not None:
                return attach_background(resp, background_tasks)
            return resp
        if not edge_url:
            # Edge ready but no signed URL (bad path / non-pximg) → local stream.
            observe_image_delivery(path="edge_unavailable")

    delivery_via_edge = bool(prefer_edge and edge_url and not prefer_edge_browser_redirect)
    if delivery_via_edge:
        assert edge_url is not None
        source_url = edge_url
        proxy_uri = None
        use_mirror = False
    else:
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
            # Explicit mirror/proxy override may still use mirror rewrite; residential only
            # when edge is not ready (default inside prepare_origin_stream).
        )

    transport = getattr(request.app.state, "httpx_transport", None)
    shared_client = getattr(request.app.state, "httpx_client", None)
    now = iso_utc_ms()
    try:
        stream_kwargs: dict[str, Any] = {
            "transport": transport,
            "client": shared_client if not proxy_uri else None,
            "proxy": proxy_uri,
            "cache_control": cache_control_stream if not delivery_via_edge else cache_control_edge,
            "range_header": request.headers.get("Range"),
        }
        if delivery_via_edge:
            # Worker attaches Pixiv Referer to origin; BFF→Worker needs none.
            stream_kwargs["referer"] = ""
        resp = await stream_url(source_url, **stream_kwargs)
        if delivery_via_edge:
            observe_image_delivery(path="edge_stream")
            try:
                resp.headers["X-Image-Edge"] = "stream"
            except Exception:
                pass
        else:
            observe_image_delivery(path="local_stream")
            if use_mirror:
                observe_image_delivery(path="local_stream_mirror")
            elif proxy_uri:
                observe_image_delivery(path="local_stream_residential")
            else:
                observe_image_delivery(path="local_stream_direct")
        if background_tasks is not None:
            # Stream proved bytes — mark ok when the row still needs it.
            schedule_mark_ok_if_needed(
                background_tasks=background_tasks,
                engine=engine,
                image_id=int(image.id),
                should_mark_ok=bool(should_mark_ok),
                now=now,
                catalog=store,
            )
            schedule_hydrate_if_needed(
                background_tasks=background_tasks,
                engine=engine,
                illust_id=int(image.illust_id),
                needs_hydrate=bool(needs_hydrate),
                hydrate_reason=str(hydrate_reason),
                queue=job_queue,
            )
            return attach_background(resp, background_tasks)
        return resp
    except Exception as exc:
        if mark_fail_on_upstream:
            if isinstance(exc, ApiError) and exc.code in {
                ErrorCode.UPSTREAM_STREAM_ERROR,
                ErrorCode.UPSTREAM_403,
                ErrorCode.UPSTREAM_404,
                ErrorCode.UPSTREAM_RATE_LIMIT,
            }:
                await best_effort(
                    store.mark_image_failure,
                    engine,
                    image_id=int(image.id),
                    now=now,
                    error_code=exc.code.value,
                    error_message=exc.message,
                    timeout_s=1.5,
                )
        raise
