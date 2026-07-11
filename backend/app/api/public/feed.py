from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request

from app.core.errors import ApiError, ErrorCode
from app.core.imgproxy import load_imgproxy_config_from_settings
from app.core.proxy_mirror import resolve_proxy_mirror
from app.core.random_delivery import schedule_pick_side_effects
from app.core.random_pick_context import build_random_pick_context
from app.core.random_query import no_match_error_from_filters
from app.core.random_request import parse_random_filters
from app.core.random_response import (
    build_feed_json_body,
    build_simple_item_payload,
    resolve_public_item_urls,
)
from app.core.runtime_config_cache import resolve_runtime_for_request
from app.db.session import create_sessionmaker

router = APIRouter()

# Keep batch modest: enough for /wtf steps, small enough for one SQLite session loop.
_FEED_LIMIT_MIN = 1
_FEED_LIMIT_MAX = 32
_FEED_LIMIT_DEFAULT = 12


@router.get("/feed")
async def feed_images(
    request: Request,
    background_tasks: BackgroundTasks,
    limit: int = _FEED_LIMIT_DEFAULT,
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
    """Batch pick for public browsers (/wtf). Same filters as /random; returns simple_json items.

    Partial results are OK when the catalog is smaller than ``limit``. Zero matches → NO_MATCH.
    """
    try:
        limit_i = int(limit)
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid limit", status_code=400) from exc
    if limit_i < _FEED_LIMIT_MIN or limit_i > _FEED_LIMIT_MAX:
        raise ApiError(
            code=ErrorCode.BAD_REQUEST,
            message=f"limit must be between {_FEED_LIMIT_MIN} and {_FEED_LIMIT_MAX}",
            status_code=400,
        )

    # Reuse /random filter parsing with fixed format=simple_json (batch is always meta+urls).
    filters = parse_random_filters(
        format="simple_json",
        redirect=0,
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
    pixiv_cat = filters.pixiv_cat
    pximg_mirror_host_override = filters.pximg_mirror_host_override

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    runtime = await resolve_runtime_for_request(request, engine)

    # Keep parity with /random query resolution (mirror/proxy flags may affect future URL policy).
    resolve_proxy_mirror(
        runtime=runtime,
        headers=request.headers,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host=pximg_mirror_host_override,
        proxy=proxy,
    )

    random_defaults = runtime.random_defaults if isinstance(runtime.random_defaults, dict) else {}
    pick_ctx = build_random_pick_context(
        filters=filters,
        random_defaults=random_defaults,
        attempts=1,
        r18_strict=r18_strict,
        strategy=strategy,
        quality_samples=quality_samples,
        query_params=request.query_params,
    )
    r18_strict = int(pick_ctx.r18_strict)

    def _no_match_error() -> ApiError:
        return no_match_error_from_filters(filters, r18_strict=int(r18_strict))

    hide_origin = bool(runtime.hide_origin_url_in_public_json)
    settings = getattr(request.app.state, "settings", None)
    httpx_client = getattr(request.app.state, "httpx_client", None)
    request_base_url = str(getattr(request, "base_url", "") or "")
    # Resolve imgproxy once per request; pass into item URL helper for reuse.
    try:
        imgproxy_cfg = load_imgproxy_config_from_settings(settings) if settings is not None else None
    except Exception:
        imgproxy_cfg = None

    def _append_item(image: Any, items_out: list[dict[str, Any]]) -> None:
        schedule_pick_side_effects(
            background_tasks=background_tasks,
            engine=engine,
            image=image,
            pick_ctx=pick_ctx,
            hydrate_reason="feed",
        )
        urls = resolve_public_item_urls(
            image=image,
            settings=settings,
            hide_origin=hide_origin,
            request_base_url=request_base_url,
            imgproxy_cfg=imgproxy_cfg,
        )
        # Feed omits per-item debug by default to cut JSON size under /wtf load.
        items_out.append(
            build_simple_item_payload(
                image=image,
                proxy_url=urls.proxy_url,
                origin_url=urls.origin_url,
                imgproxy_url=urls.imgproxy_url,
                debug=None,
                local_url=urls.local_url,
            )
        )

    items: list[dict[str, Any]] = []
    exclude_ids: list[int] = []

    async with Session() as session:
        # Prefer one engine batch pick when dual-run is enabled (limit>1).
        images, _eng_meta = await pick_ctx.try_engine_batch(
            session=session,
            settings=settings,
            httpx_client=httpx_client,
            filters=filters,
            limit=limit_i,
        )
        for image in images:
            exclude_ids.append(int(image.id))
            _append_item(image, items)

        # Python loop: full path when engine off/failed, or top-up when engine returned partial.
        remaining = limit_i - len(items)
        if remaining > 0:
            for _ in range(remaining):
                image, _debug = await pick_ctx.pick(
                    session=session,
                    settings=settings,
                    httpx_client=httpx_client,
                    filters=filters,
                    exclude_image_ids=list(exclude_ids) if exclude_ids else None,
                )
                if image is None:
                    break
                exclude_ids.append(int(image.id))
                _append_item(image, items)

    if not items:
        raise _no_match_error()

    request_id = getattr(getattr(request, "state", None), "request_id", None) or "req_unknown"
    return build_feed_json_body(request_id=request_id, items=items, requested=limit_i)
