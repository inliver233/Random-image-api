from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request

from app.core.errors import ApiError
from app.core.image_edge import resolve_image_edge_redirect_url
from app.core.metrics import observe_image_delivery
from app.core.proxy_mirror import resolve_proxy_mirror
from app.core.random_delivery import (
    attach_background,
    build_edge_redirect_response,
    deliver_random_image_stream,
    schedule_edge_side_effects,
)
from app.core.random_pick_context import build_random_pick_context
from app.core.random_query import no_match_error_from_filters
from app.core.random_request import (
    build_local_i_redirect_response,
    force_local_from_query,
    parse_public_debug_flag,
    parse_random_filters,
    prefer_image_edge,
)
from app.core.random_response import build_json_body, build_simple_json_body, resolve_public_item_urls
from app.core.random_strategy import needs_opportunistic_hydrate
from app.core.runtime_config_cache import resolve_runtime_for_request
from app.db.tags_get import get_tag_names_for_image
from app.db.session import create_sessionmaker

router = APIRouter()


@router.get("/random")
async def random_image(
    request: Request,
    background_tasks: BackgroundTasks,
    format: str = "image",
    redirect: int = 0,
    attempts: int | None = None,
    seed: str | None = None,
    strategy: str | None = None,
    quality_samples: int | None = None,
    r18: int = 0,
    r18_strict: int | None = None,
    ai_type: str = "any",
    illust_type: str = "any",
    orientation: str = "any",
    layout: str | None = None,
    adaptive: int = 0,
    pixiv_cat: int = 0,
    pximg_mirror_host: str | None = None,
    proxy: str | None = None,
    min_width: int = 0,
    min_height: int = 0,
    min_pixels: int = 0,
    min_bookmarks: int = 0,
    min_views: int = 0,
    min_comments: int = 0,
    included_tags: list[str] | None = Query(default=None),
    excluded_tags: list[str] | None = Query(default=None),
    user_id: int | None = None,
    illust_id: int | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> Any:
    filters = parse_random_filters(
        format=format,
        redirect=redirect,
        seed=seed,
        r18=r18,
        ai_type=ai_type,
        illust_type=illust_type,
        orientation=orientation,
        layout=layout,
        adaptive=adaptive,
        pixiv_cat=pixiv_cat,
        pximg_mirror_host=pximg_mirror_host,
        min_width=min_width,
        min_height=min_height,
        min_pixels=min_pixels,
        min_bookmarks=min_bookmarks,
        min_views=min_views,
        min_comments=min_comments,
        included_tags=included_tags,
        excluded_tags=excluded_tags,
        user_id=user_id,
        illust_id=illust_id,
        created_from=created_from,
        created_to=created_to,
        query_params=request.query_params,
        headers=request.headers,
    )
    format = filters.format
    redirect = filters.redirect
    pixiv_cat = filters.pixiv_cat
    pximg_mirror_host_override = filters.pximg_mirror_host_override

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    runtime = await resolve_runtime_for_request(request, engine)

    resolved_proxy = resolve_proxy_mirror(
        runtime=runtime,
        headers=request.headers,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host=pximg_mirror_host_override,
        proxy=proxy,
    )
    proxy_override = resolved_proxy.proxy_override
    # Prefer explicit query override; fall back to shared resolver (proxy= may imply mirror).
    pximg_mirror_host_override = (
        resolved_proxy.pximg_mirror_host_override or pximg_mirror_host_override
    )
    use_pixiv_cat = resolved_proxy.use_pixiv_cat
    mirror_host = resolved_proxy.mirror_host
    # Shared for redirect=1 and format=image stream so local=1 is never path-dependent.
    force_local = force_local_from_query(request.query_params)

    random_defaults = runtime.random_defaults if isinstance(runtime.random_defaults, dict) else {}
    pick_ctx = build_random_pick_context(
        filters=filters,
        random_defaults=random_defaults,
        attempts=attempts,
        r18_strict=r18_strict,
        strategy=strategy,
        quality_samples=quality_samples,
        query_params=request.query_params,
    )
    # Keep no-match filter summary in sync with resolved default when query omits r18_strict.
    r18_strict = int(pick_ctx.r18_strict)

    def _no_match_error() -> ApiError:
        return no_match_error_from_filters(filters, r18_strict=int(r18_strict))

    async def _pick_with_strategy(
        *,
        session: Any,
        exclude_image_ids: list[int] | None = None,
    ) -> tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]:
        return await pick_ctx.pick(
            session=session,
            settings=getattr(request.app.state, "settings", None),
            httpx_client=getattr(request.app.state, "httpx_client", None),
            filters=filters,
            exclude_image_ids=exclude_image_ids,
        )

    if format in {"json", "simple_json"} or (format == "image" and redirect == 1):
        tags: list[str] = []
        debug: dict[str, Any] = {}
        async with Session() as session:
            image, debug = await _pick_with_strategy(session=session)
            if image is None:
                raise _no_match_error()
            if format == "json":
                tags = await get_tag_names_for_image(session, image_id=image.id)

        # JSON/redirect never prove bytes — never mark_image_ok here.
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
            hydrate_reason="random",
            mark_ok_on_edge=False,
            should_mark_ok=False,
        )

        if format == "image" and redirect == 1:
            # Prefer CF image edge as primary public delivery when configured.
            # Explicit local mirror/proxy overrides (incl. local=1) keep the local /i/ fallback path.
            prefer_edge = prefer_image_edge(
                proxy_override=proxy_override,
                pixiv_cat=int(pixiv_cat),
                pximg_mirror_host_override=pximg_mirror_host_override,
                force_local=force_local,
            )
            edge_url = (
                resolve_image_edge_redirect_url(
                    settings=request.app.state.settings,
                    original_url=str(image.original_url),
                )
                if prefer_edge
                else None
            )
            if edge_url:
                # Edge 302 does not prove bytes; skip mark_image_ok (fail_cooldown stays honest).
                observe_image_delivery(path="edge_redirect")
                resp = build_edge_redirect_response(edge_url=edge_url, cache_control="no-store")
            else:
                if prefer_edge:
                    # Prefer edge but no signed URL → local /i redirect cascade.
                    observe_image_delivery(path="edge_unavailable")
                observe_image_delivery(path="local_i_redirect")
                resp = build_local_i_redirect_response(
                    image_id=int(image.id),
                    ext=str(image.ext),
                    proxy_override=proxy_override,
                    pixiv_cat=int(pixiv_cat),
                    pximg_mirror_host_override=pximg_mirror_host_override,
                )
            return attach_background(resp, background_tasks)

        urls = resolve_public_item_urls(
            image=image,
            settings=request.app.state.settings,
            hide_origin=bool(runtime.hide_origin_url_in_public_json),
            request_base_url=str(getattr(request, "base_url", "") or ""),
        )
        request_id = getattr(getattr(request, "state", None), "request_id", None) or "req_unknown"
        # Public JSON omits debug unless ?debug=1 (keeps payloads small for browsers).
        debug_out = debug if parse_public_debug_flag(request.query_params) else None
        if format == "simple_json":
            return build_simple_json_body(
                request_id=request_id,
                image=image,
                proxy_url=urls.proxy_url,
                origin_url=urls.origin_url,
                imgproxy_url=urls.imgproxy_url,
                debug=debug_out,
                local_url=urls.local_url,
            )

        return build_json_body(
            request_id=request_id,
            image=image,
            tags=tags,
            proxy_url=urls.proxy_url,
            origin_url=urls.origin_url,
            imgproxy_url=urls.imgproxy_url,
            debug=debug_out,
            local_url=urls.local_url,
        )

    # When edge is enabled and client did not force local mirror/proxy, hand bytes off to CF.
    prefer_edge_redirect = prefer_image_edge(
        proxy_override=proxy_override,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host_override=pximg_mirror_host_override,
        force_local=force_local,
    )

    async def _pick_for_delivery(*, session: Any, exclude_image_ids: list[int] | None = None):
        return await _pick_with_strategy(session=session, exclude_image_ids=exclude_image_ids)

    return await deliver_random_image_stream(
        pick=_pick_for_delivery,
        Session=Session,
        engine=engine,
        settings=request.app.state.settings,
        runtime=runtime,
        httpx_transport=getattr(request.app.state, "httpx_transport", None),
        httpx_client=getattr(request.app.state, "httpx_client", None),
        range_header=request.headers.get("Range"),
        attempts=int(pick_ctx.attempts),
        prefer_edge_redirect=prefer_edge_redirect,
        use_pixiv_cat=use_pixiv_cat,
        mirror_host=mirror_host,
        anti_repeat_enabled=bool(pick_ctx.anti_repeat_enabled),
        dedup_window_s=float(pick_ctx.dedup_window_s),
        dedup_max_images=int(pick_ctx.dedup_max_images),
        dedup_max_authors=int(pick_ctx.dedup_max_authors),
        background_tasks=background_tasks,
        no_match_error=_no_match_error,
    )
