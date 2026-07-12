from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from app.core.errors import ApiError, ErrorCode
from app.core.imgproxy import load_imgproxy_config_from_settings
from app.core.proxy_mirror import resolve_proxy_mirror
from app.core.random_delivery import (
    resolve_catalog_store,
    resolve_random_service_factory,
    resolve_recent_dedup,
    schedule_pick_side_effects,
)
from app.core.random_query import no_match_error_from_filters
from app.core.random_request import PublicRandomQuery, parse_public_debug_flag
from app.core.random_response import (
    build_feed_json_body,
    build_simple_item_payload,
    resolve_public_item_urls,
)
from app.core.runtime_config_cache import resolve_runtime_for_request
from app.db.session import resolve_sessionmaker
router = APIRouter()

# Keep batch modest: enough for /wtf steps, small enough for one SQLite session loop.
_FEED_LIMIT_MIN = 1
_FEED_LIMIT_MAX = 32
_FEED_LIMIT_DEFAULT = 12


@router.get(
    "/feed",
    summary="Batch pick for public browsers",
    description=(
        "Batch pick for /wtf. Items stay lean (no per-item debug). "
        "With `debug=1`, envelope-only `data.debug` exposes dual-run batch honesty "
        "(`engine_status`, `batch_count`, `topup_count`, `topup_skip_engine`)."
    ),
)
async def feed_images(
    request: Request,
    background_tasks: BackgroundTasks,
    q: PublicRandomQuery = Depends(),
    limit: int = _FEED_LIMIT_DEFAULT,
    debug: int | None = Query(
        default=None,
        description=(
            "When 1/true/on, attach envelope dual-run debug (`data.debug.engine_status` etc.). "
            "Default off; items never carry per-item debug."
        ),
    ),
) -> Any:
    """Batch pick for public browsers (/wtf). Same filters as /random; returns simple_json items.

    Partial results are OK when the catalog is smaller than ``limit``. Zero matches → NO_MATCH.
    """
    # OpenAPI documents `debug`; runtime truthiness uses parse_public_debug_flag(query_params).
    _ = debug
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

    # Shared filter dependency with fixed format=simple_json (batch is always meta+urls).
    filters = q.parse_filters(
        format="simple_json",
        redirect=0,
        query_params=request.query_params,
        headers=request.headers,
    )
    pixiv_cat = filters.pixiv_cat
    pximg_mirror_host_override = filters.pximg_mirror_host_override

    engine = request.app.state.engine
    catalog = resolve_catalog_store(getattr(request.app.state, "catalog_store", None))
    recent_dedup = resolve_recent_dedup(getattr(request.app.state, "recent_dedup", None))
    random_service = resolve_random_service_factory(getattr(request.app.state, "random_service", None))
    random_pick = getattr(request.app.state, "random_pick", None)
    job_queue = getattr(request.app.state, "job_queue", None)
    Session = resolve_sessionmaker(request, engine)
    runtime = await resolve_runtime_for_request(request, engine)

    # Keep parity with /random query resolution (mirror/proxy flags may affect future URL policy).
    resolve_proxy_mirror(
        runtime=runtime,
        headers=request.headers,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host=pximg_mirror_host_override,
        proxy=q.proxy,
    )

    random_defaults = runtime.random_defaults if isinstance(runtime.random_defaults, dict) else {}
    pick_ctx = random_service.build_context(
        filters=filters,
        random_defaults=random_defaults,
        attempts=1,
        r18_strict=q.r18_strict,
        strategy=q.strategy,
        quality_samples=q.quality_samples,
        query_params=request.query_params,
        recent_dedup=recent_dedup,
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
            catalog=catalog,
            recent_dedup=recent_dedup,
            job_queue=job_queue,
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
    include_debug = parse_public_debug_flag(request.query_params)
    eng_meta: dict[str, Any] | None = None
    batch_count = 0
    topup_count = 0

    async with Session() as session:
        # Prefer one engine batch pick when dual-run is enabled (limit>1).
        images, eng_meta = await pick_ctx.try_engine_batch(
            session=session,
            settings=settings,
            httpx_client=httpx_client,
            filters=filters,
            limit=limit_i,
            catalog=catalog,
        )
        batch_count = len(images)
        for image in images:
            exclude_ids.append(int(image.id))
            _append_item(image, items)

        # Python loop: full path when engine off/failed, or top-up when engine returned partial.
        # After one engine batch attempt, top-up must not re-hit dual-run N times.
        remaining = limit_i - len(items)
        if remaining > 0:
            skip_engine_topup = True  # batch already tried (or dual-run off → no-op skip)
            for _ in range(remaining):
                image, _debug = await pick_ctx.pick(
                    session=session,
                    settings=settings,
                    httpx_client=httpx_client,
                    filters=filters,
                    exclude_image_ids=list(exclude_ids) if exclude_ids else None,
                    catalog=catalog,
                    pick=random_pick,
                    skip_engine=skip_engine_topup,
                )
                if image is None:
                    break
                exclude_ids.append(int(image.id))
                _append_item(image, items)
                topup_count += 1

    if not items:
        raise _no_match_error()

    request_id = getattr(getattr(request, "state", None), "request_id", None) or "req_unknown"
    # Envelope-only debug (items stay lean for /wtf). Surfaces dual-run batch engine_status.
    debug_out: dict[str, Any] | None = None
    if include_debug:
        status: str | None = None
        if isinstance(eng_meta, dict):
            raw_status = eng_meta.get("engine_status")
            if isinstance(raw_status, str) and raw_status.strip():
                status = raw_status.strip()
        debug_out = {
            "batch": True,
            # None eng_meta ⇒ dual-run not routed (disabled / no URL / traffic miss).
            "engine_status": status or "skipped_not_routed",
            "batch_count": int(batch_count),
            "topup_count": int(topup_count),
            # Feed always sticky-skips engine on Python top-up after one batch attempt.
            "topup_skip_engine": True,
        }
        if isinstance(eng_meta, dict):
            picked_by = eng_meta.get("picked_by")
            if isinstance(picked_by, str) and picked_by.strip():
                debug_out["picked_by"] = picked_by.strip()
    return build_feed_json_body(
        request_id=request_id,
        items=items,
        requested=limit_i,
        debug=debug_out,
    )
