from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import RedirectResponse

from app.core.errors import ApiError, ErrorCode
from app.core.http_stream import stream_url
from app.core.image_edge import resolve_image_edge_redirect_url, resolve_public_proxy_url
from app.core.imgproxy import build_signed_processing_url, load_imgproxy_config_from_settings
from app.core.pximg_reverse_proxy import (
    normalize_pximg_proxy,
    pick_pximg_mirror_host_for_request,
    rewrite_pximg_to_mirror,
)
from app.core.proxy_routing import select_proxy_uri_for_url
from app.core.recent_dedup import get_recent_lists, record_recent
from app.core.recommendation import quality_score
from app.core.random_defaults import (
    resolve_attempts,
    resolve_dedup,
    resolve_fail_cooldown_ms,
    resolve_quality_samples,
    resolve_r18_strict,
    resolve_recommendation_config,
    resolve_strategy,
)
from app.core.random_engine_pick import (
    build_engine_filters,
    build_engine_pick_payload,
    build_engine_quality_params,
    try_pick_via_engine,
)
from app.core.random_query import build_no_match_error
from app.core.random_request import local_i_query_string, parse_random_filters, prefer_image_edge
from app.core.random_response import build_json_body, build_simple_json_body
from app.core.random_strategy import needs_opportunistic_hydrate, pick_by_quality, pick_by_random_key
from app.core.runtime_config_cache import get_cached_runtime_config
from app.core.time import iso_utc_ms
from app.db.images_mark import mark_image_failure, mark_image_ok
from app.db.tags_get import get_tag_names_for_image
from app.db.session import create_sessionmaker
from app.jobs.enqueue import enqueue_opportunistic_hydrate_metadata

router = APIRouter()

# Cap NOT IN size for SQLite plan quality; remaining recent ids still apply logit penalties.
_RECENT_EXCLUDE_SQL_CAP = 512

# Compatibility alias for existing tests (prefer app.core.recommendation.quality_score going forward).
_quality_score = quality_score


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
    seed_norm = filters.seed_norm
    ai_type_raw = filters.ai_type_raw
    ai_type_i = filters.ai_type_i
    illust_type_raw = filters.illust_type_raw
    illust_type_i = filters.illust_type_i
    r18 = filters.r18
    adaptive = filters.adaptive
    pixiv_cat = filters.pixiv_cat
    pximg_mirror_host_override = filters.pximg_mirror_host_override
    layout_norm = filters.layout_norm
    orientation_map = filters.orientation_map
    min_width_i = filters.min_width_i
    min_height_i = filters.min_height_i
    min_pixels_i = filters.min_pixels_i
    min_bookmarks_i = filters.min_bookmarks_i
    min_views_i = filters.min_views_i
    min_comments_i = filters.min_comments_i
    included = filters.included
    excluded = filters.excluded
    user_id = filters.user_id
    illust_id = filters.illust_id
    created_from_norm = filters.created_from_norm
    created_to_norm = filters.created_to_norm

    def _no_match_error() -> ApiError:
        return build_no_match_error(
            r18=r18,
            r18_strict=int(r18_strict) if r18_strict is not None else 1,
            ai_type_raw=ai_type_raw,
            illust_type_raw=illust_type_raw,
            adaptive=int(adaptive),
            layout_norm=layout_norm,
            min_width_i=int(min_width_i),
            min_height_i=int(min_height_i),
            min_pixels_i=int(min_pixels_i),
            min_bookmarks_i=int(min_bookmarks_i),
            min_views_i=int(min_views_i),
            min_comments_i=int(min_comments_i),
            included=included,
            excluded=excluded,
            user_id=user_id,
            illust_id=illust_id,
            created_from_norm=created_from_norm,
            created_to_norm=created_to_norm,
            ai_type_i=ai_type_i,
            illust_type_i=illust_type_i,
        )

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    cache = getattr(request.app.state, "runtime_config_cache", None)
    if cache is not None:
        runtime = await cache.get(engine)
    else:
        runtime = await get_cached_runtime_config(engine)

    proxy_override: str | None = None
    if proxy is not None:
        raw = str(proxy or "").strip()
        if raw:
            proxy_override = normalize_pximg_proxy(raw, extra_hosts=runtime.image_proxy_extra_pximg_mirror_hosts)
            if proxy_override is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported proxy", status_code=400)

    mirror_host_override = proxy_override or pximg_mirror_host_override
    use_pixiv_cat = bool(runtime.image_proxy_use_pixiv_cat) or int(pixiv_cat) == 1 or proxy_override is not None
    runtime_mirror_host = str(getattr(runtime, "image_proxy_pximg_mirror_host", "") or "").strip() or "i.pixiv.cat"
    mirror_host = mirror_host_override or (
        pick_pximg_mirror_host_for_request(headers=request.headers, fallback_host=runtime_mirror_host)
        if use_pixiv_cat
        else runtime_mirror_host
    )

    async def _best_effort(fn, *args, timeout_s: float = 1.5, **kwargs) -> None:  # type: ignore[no-untyped-def]
        try:
            await asyncio.wait_for(fn(*args, **kwargs), timeout=float(timeout_s))
        except Exception:
            pass

    random_defaults = runtime.random_defaults if isinstance(runtime.random_defaults, dict) else {}

    attempts_resolved = resolve_attempts(attempts, random_defaults)
    attempts_source = attempts_resolved.source
    attempts = int(attempts_resolved.value)

    r18_strict_resolved = resolve_r18_strict(r18_strict, random_defaults)
    r18_strict_source = r18_strict_resolved.source
    r18_strict = int(r18_strict_resolved.value)

    fail_cooldown_ms_i, fail_cooldown_source, fail_cooldown_before = resolve_fail_cooldown_ms(random_defaults)

    pick_kwargs: dict[str, Any] = {
        "r18": r18,
        "r18_strict": bool(r18_strict),
        "ai_type": ai_type_i,
        "illust_type": illust_type_i,
        "orientation": orientation_map[layout_norm],
        "min_width": int(min_width_i),
        "min_height": int(min_height_i),
        "min_pixels": int(min_pixels_i),
        "min_bookmarks": int(min_bookmarks_i),
        "min_views": int(min_views_i),
        "min_comments": int(min_comments_i),
        "included_tags": included,
        "excluded_tags": excluded,
        "user_id": user_id,
        "illust_id": illust_id,
        "created_from": created_from_norm,
        "created_to": created_to_norm,
        "fail_cooldown_before": fail_cooldown_before,
    }

    rng = random.Random(seed_norm) if seed_norm else random
    time_boost_enabled = not bool(seed_norm)
    dedup = resolve_dedup(random_defaults)
    dedup_enabled_setting = bool(dedup.enabled)
    dedup_window_s = float(dedup.window_s)
    dedup_max_images = int(dedup.max_images)
    dedup_max_authors = int(dedup.max_authors)
    dedup_strict = bool(dedup.strict)
    dedup_image_penalty = float(dedup.image_penalty)
    dedup_author_penalty = float(dedup.author_penalty)

    anti_repeat_enabled = bool(dedup_enabled_setting) and bool(time_boost_enabled) and user_id is None and illust_id is None
    recent_image_ids: set[int] = set()
    recent_author_ids: set[int] = set()
    recent_exclude_image_ids: list[int] = []
    if anti_repeat_enabled:
        recent_image_list, recent_author_list = get_recent_lists(
            time.monotonic(),
            window_s=float(dedup_window_s),
            max_images=int(dedup_max_images),
            max_authors=int(dedup_max_authors),
        )
        recent_image_ids = set(int(x) for x in recent_image_list)
        recent_author_ids = set(int(x) for x in recent_author_list)
        if recent_image_list:
            # Prefer newest ids for hard SQL exclusion.
            recent_exclude_image_ids = list(dict.fromkeys(int(x) for x in recent_image_list[-_RECENT_EXCLUDE_SQL_CAP:]))

    strategy_norm, strategy_source = resolve_strategy(strategy, random_defaults)

    quality_plan = resolve_quality_samples(
        quality_samples=quality_samples,
        random_defaults=random_defaults,
        strategy_norm=strategy_norm,
        time_boost_enabled=bool(time_boost_enabled),
        included=included,
        excluded=excluded,
        min_bookmarks_i=int(min_bookmarks_i),
        min_views_i=int(min_views_i),
        min_comments_i=int(min_comments_i),
        min_pixels_i=int(min_pixels_i),
        min_width_i=int(min_width_i),
        min_height_i=int(min_height_i),
        ai_type_i=ai_type_i,
        illust_type_i=illust_type_i,
        orientation_set=orientation_map[layout_norm] is not None,
        created_from_norm=created_from_norm,
        created_to_norm=created_to_norm,
        r18=int(r18),
        anti_repeat_enabled=bool(anti_repeat_enabled),
    )
    quality_samples_i = int(quality_plan.samples)
    quality_samples_base = int(quality_plan.base)
    quality_samples_multiplier = int(quality_plan.multiplier)
    quality_samples_scaled = bool(quality_plan.scaled)
    quality_samples_source = quality_plan.source

    rec_cfg = resolve_recommendation_config(
        random_defaults=random_defaults,
        query_params=getattr(request, "query_params", None),
    )
    recommendation_source = rec_cfg.source
    rec_override_keys = rec_cfg.query_override_keys
    pick_mode_raw = rec_cfg.pick_mode
    temperature = float(rec_cfg.temperature)
    score_weights = rec_cfg.score_weights
    multipliers = rec_cfg.multipliers
    freshness_half_life_days = float(rec_cfg.freshness_half_life_days)
    velocity_smooth_days = float(rec_cfg.velocity_smooth_days)

    debug_base = {
        "attempts": int(attempts),
        "attempts_source": attempts_source,
        "r18_strict": int(r18_strict),
        "r18_strict_source": r18_strict_source,
        "fail_cooldown_ms": int(fail_cooldown_ms_i),
        "fail_cooldown_source": fail_cooldown_source,
        "strategy": strategy_norm,
        "strategy_source": strategy_source,
        "quality_samples": int(quality_samples_i),
        "quality_samples_base": int(quality_samples_base),
        "quality_samples_multiplier": int(quality_samples_multiplier),
        "quality_samples_scaled": bool(quality_samples_scaled),
        "quality_samples_source": quality_samples_source,
        "anti_repeat_enabled": bool(anti_repeat_enabled),
        "dedup_enabled": bool(dedup_enabled_setting),
        "dedup_window_s": float(dedup_window_s),
        "dedup_max_images": int(dedup_max_images),
        "dedup_max_authors": int(dedup_max_authors),
        "dedup_strict": bool(dedup_strict),
        "dedup_image_penalty": float(dedup_image_penalty),
        "dedup_author_penalty": float(dedup_author_penalty),
        "time_boost_enabled": bool(time_boost_enabled),
        "recommendation_source": recommendation_source,
        "recommendation_query_overrides": list(rec_override_keys or []),
        "freshness_half_life_days": float(freshness_half_life_days),
        "velocity_smooth_days": float(velocity_smooth_days),
    }

    async def _pick_with_strategy(
        *,
        session: Any,
        exclude_image_ids: list[int] | None = None,
    ) -> tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]:
        # Optional Go random-engine path (feature flag). On any miss/unavailable, fall back to Python.
        settings = getattr(request.app.state, "settings", None)
        engine_enabled = bool(getattr(settings, "random_engine_enabled", False))
        engine_url = str(getattr(settings, "random_engine_url", "") or "").strip().rstrip("/")
        httpx_client = getattr(request.app.state, "httpx_client", None)
        if engine_enabled and engine_url and httpx_client is not None:
            base_exclude = list(exclude_image_ids or [])
            exclude_set: set[int] = set(int(x) for x in base_exclude)
            if bool(anti_repeat_enabled) and recent_exclude_image_ids:
                exclude_set.update(int(x) for x in recent_exclude_image_ids)

            engine_filters = build_engine_filters(
                r18=int(r18),
                r18_strict=int(r18_strict),
                ai_type_raw=ai_type_raw,
                ai_type_i=ai_type_i,
                illust_type_i=illust_type_i,
                orientation_code=orientation_map[layout_norm],
                min_width_i=int(min_width_i),
                min_height_i=int(min_height_i),
                min_pixels_i=int(min_pixels_i),
                min_bookmarks_i=int(min_bookmarks_i),
                min_views_i=int(min_views_i),
                min_comments_i=int(min_comments_i),
                included=included,
                excluded=excluded,
                exclude_image_ids=exclude_set,
                user_id=user_id,
                illust_id=illust_id,
                created_from_norm=created_from_norm,
                created_to_norm=created_to_norm,
                fail_cooldown_before=fail_cooldown_before,
            )
            quality_params = build_engine_quality_params(
                strategy_norm=strategy_norm,
                quality_samples_i=int(quality_samples_i),
                pick_mode_raw=pick_mode_raw,
                temperature=float(temperature),
                score_weights=score_weights,
                multipliers=multipliers,
                freshness_half_life_days=float(freshness_half_life_days),
                velocity_smooth_days=float(velocity_smooth_days),
            )

            payload = build_engine_pick_payload(
                filters=engine_filters,
                strategy=strategy_norm,
                quality=quality_params,
                seed=seed_norm or None,
                limit=1,
                debug=False,
            )
            timeout_s = float(getattr(settings, "random_engine_timeout_ms", 800) or 800) / 1000.0
            image, eng_meta = await try_pick_via_engine(
                client=httpx_client,
                base_url=engine_url,
                session=session,
                payload=payload,
                timeout_s=timeout_s,
            )
            if image is not None:
                return image, {**debug_base, "attempts_used": 1, **eng_meta}
            # Soft no-match from a healthy engine: still fall back to Python (index may be stale).
            # Hard unavailable also falls through.

        if strategy_norm == "random":
            return await pick_by_random_key(
                session=session,
                rng=rng,
                pick_kwargs=pick_kwargs,
                exclude_image_ids=exclude_image_ids,
                anti_repeat_enabled=bool(anti_repeat_enabled),
                recent_exclude_image_ids=recent_exclude_image_ids,
                dedup_strict=bool(dedup_strict),
                debug_base=debug_base,
            )

        return await pick_by_quality(
            session=session,
            rng=rng,
            pick_kwargs=pick_kwargs,
            exclude_image_ids=exclude_image_ids,
            anti_repeat_enabled=bool(anti_repeat_enabled),
            recent_exclude_image_ids=recent_exclude_image_ids,
            recent_image_ids=recent_image_ids,
            recent_author_ids=recent_author_ids,
            dedup_strict=bool(dedup_strict),
            dedup_image_penalty=float(dedup_image_penalty),
            dedup_author_penalty=float(dedup_author_penalty),
            quality_samples_i=int(quality_samples_i),
            pick_mode_raw=pick_mode_raw,
            temperature=float(temperature),
            score_weights=score_weights,
            multipliers=multipliers,
            freshness_half_life_days=float(freshness_half_life_days),
            velocity_smooth_days=float(velocity_smooth_days),
            time_boost_enabled=bool(time_boost_enabled),
            debug_base=debug_base,
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

        if bool(anti_repeat_enabled):
            try:
                record_recent(
                    now=time.monotonic(),
                    image_id=int(image.id),
                    user_id=int(image.user_id) if getattr(image, "user_id", None) is not None else None,
                    window_s=float(dedup_window_s),
                    max_images=int(dedup_max_images),
                    max_authors=int(dedup_max_authors),
                )
            except Exception:
                pass

        if needs_opportunistic_hydrate(image):
            background_tasks.add_task(
                _best_effort,
                enqueue_opportunistic_hydrate_metadata,
                engine,
                illust_id=int(image.illust_id),
                reason="random",
                timeout_s=2.5,
            )

        if format == "image" and redirect == 1:
            # Prefer CF image edge as primary public delivery when configured.
            # Explicit local mirror/proxy overrides keep the local /i/ fallback path.
            edge_url = None
            if prefer_image_edge(
                proxy_override=proxy_override,
                pixiv_cat=int(pixiv_cat),
                pximg_mirror_host_override=pximg_mirror_host_override,
            ):
                edge_url = resolve_image_edge_redirect_url(
                    settings=request.app.state.settings,
                    original_url=str(image.original_url),
                )
            if edge_url:
                resp = RedirectResponse(
                    url=edge_url,
                    status_code=302,
                    headers={"Cache-Control": "no-store", "X-Image-Edge": "1"},
                )
            else:
                qs = local_i_query_string(
                    proxy_override=proxy_override,
                    pixiv_cat=int(pixiv_cat),
                    pximg_mirror_host_override=pximg_mirror_host_override,
                )
                resp = RedirectResponse(
                    url=f"/i/{image.id}.{image.ext}{qs}",
                    status_code=302,
                    headers={"Cache-Control": "no-store"},
                )
            try:
                if getattr(resp, "background", None) is None:
                    resp.background = background_tasks
            except Exception:
                pass
            return resp

        origin_url = None if runtime.hide_origin_url_in_public_json else image.original_url

        imgproxy_url = None
        try:
            cfg = load_imgproxy_config_from_settings(request.app.state.settings)
        except Exception:
            cfg = None
        if cfg is not None:
            try:
                if runtime.hide_origin_url_in_public_json:
                    base = str(getattr(request, "base_url", "") or "").rstrip("/")
                    source_url = f"{base}/i/{image.id}.{image.ext}"
                else:
                    source_url = str(image.original_url)
                imgproxy_url = build_signed_processing_url(cfg, source_url=source_url, extension=str(image.ext))
            except Exception:
                imgproxy_url = None

        request_id = getattr(getattr(request, "state", None), "request_id", None) or "req_unknown"
        proxy_url = resolve_public_proxy_url(
            settings=request.app.state.settings,
            original_url=str(image.original_url),
            local_proxy_path=f"/i/{image.id}.{image.ext}",
        )
        if format == "simple_json":
            return build_simple_json_body(
                request_id=request_id,
                image=image,
                proxy_url=proxy_url,
                origin_url=origin_url,
                imgproxy_url=imgproxy_url,
                debug=debug,
            )

        return build_json_body(
            request_id=request_id,
            image=image,
            tags=tags,
            proxy_url=proxy_url,
            origin_url=origin_url,
            imgproxy_url=imgproxy_url,
            debug=debug,
        )

    tried_ids: set[int] = set()
    last_error: ApiError | None = None
    attempts_i = int(attempts)
    runtime_stream = runtime
    # When edge is enabled and client did not force local mirror/proxy, hand bytes off to CF.
    force_local = str(request.query_params.get("local") or "").strip().lower() in {"1", "true", "yes"}
    prefer_edge_redirect = prefer_image_edge(
        proxy_override=proxy_override,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host_override=pximg_mirror_host_override,
        force_local=force_local,
    )

    for _ in range(attempts_i):
        async with Session() as session:
            image, _debug = await _pick_with_strategy(session=session, exclude_image_ids=list(tried_ids))
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
            edge_url = resolve_image_edge_redirect_url(
                settings=request.app.state.settings,
                original_url=origin_url,
            )
            if edge_url:
                if bool(anti_repeat_enabled):
                    try:
                        record_recent(
                            now=time.monotonic(),
                            image_id=int(image_id),
                            user_id=user_id_for_recent,
                            window_s=float(dedup_window_s),
                            max_images=int(dedup_max_images),
                            max_authors=int(dedup_max_authors),
                        )
                    except Exception:
                        pass
                if should_mark_ok:
                    background_tasks.add_task(
                        _best_effort, mark_image_ok, engine, image_id=image_id, now=iso_utc_ms(), timeout_s=1.5
                    )
                if needs_hydrate:
                    background_tasks.add_task(
                        _best_effort,
                        enqueue_opportunistic_hydrate_metadata,
                        engine,
                        illust_id=illust_id_for_hydrate,
                        reason="random",
                        timeout_s=2.5,
                    )
                resp = RedirectResponse(
                    url=edge_url,
                    status_code=302,
                    headers={"Cache-Control": "no-store", "X-Image-Edge": "1"},
                )
                try:
                    if getattr(resp, "background", None) is None:
                        resp.background = background_tasks
                except Exception:
                    pass
                return resp

        transport = getattr(request.app.state, "httpx_transport", None)
        shared_client = getattr(request.app.state, "httpx_client", None)
        proxy_uri = None
        if not use_pixiv_cat:
            picked = await select_proxy_uri_for_url(
                engine,
                request.app.state.settings,
                runtime_stream,
                url=origin_url,
            )
            if picked is not None:
                proxy_uri = picked.uri
        try:
            resp = await stream_url(
                source_url,
                transport=transport,
                client=shared_client if not proxy_uri else None,
                proxy=proxy_uri,
                cache_control="no-store",
                range_header=request.headers.get("Range"),
            )
            if bool(anti_repeat_enabled):
                try:
                    record_recent(
                        now=time.monotonic(),
                        image_id=int(image_id),
                        user_id=user_id_for_recent,
                        window_s=float(dedup_window_s),
                        max_images=int(dedup_max_images),
                        max_authors=int(dedup_max_authors),
                    )
                except Exception:
                    pass
            if should_mark_ok:
                background_tasks.add_task(_best_effort, mark_image_ok, engine, image_id=image_id, now=iso_utc_ms(), timeout_s=1.5)
            if needs_hydrate:
                background_tasks.add_task(
                    _best_effort,
                    enqueue_opportunistic_hydrate_metadata,
                    engine,
                    illust_id=illust_id_for_hydrate,
                    reason="random",
                    timeout_s=2.5,
                )
            try:
                if getattr(resp, "background", None) is None:
                    resp.background = background_tasks
            except Exception:
                pass
            return resp
        except ApiError as exc:
            if exc.code in {
                ErrorCode.UPSTREAM_STREAM_ERROR,
                ErrorCode.UPSTREAM_403,
                ErrorCode.UPSTREAM_404,
                ErrorCode.UPSTREAM_RATE_LIMIT,
            }:
                await _best_effort(
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
        raise _no_match_error()

    raise ApiError(
        code=ErrorCode.UPSTREAM_STREAM_ERROR,
        message="多次尝试后上游请求仍失败。",
        status_code=502,
        details={
            "attempts_used": len(tried_ids),
            "last_upstream_code": last_error.code.value,
        },
    )
