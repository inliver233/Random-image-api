from __future__ import annotations

import asyncio
import random
import os
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import RedirectResponse

from app.core.errors import ApiError, ErrorCode
from app.core.http_stream import stream_url
from app.core.image_edge import resolve_public_proxy_url
from app.core.imgproxy import build_signed_processing_url, load_imgproxy_config_from_settings
from app.core.pximg_reverse_proxy import (
    normalize_pximg_mirror_host,
    normalize_pximg_proxy,
    pick_pximg_mirror_host_for_request,
    rewrite_pximg_to_mirror,
)
from app.core.proxy_routing import select_proxy_uri_for_url
from app.core.recent_dedup import get_recent_lists, get_recent_sets, record_recent
from app.core.recommendation import (
    DEFAULT_RECOMMENDATION,
    DEFAULT_SCORE_WEIGHTS,
    as_bool,
    as_float,
    multiplier_for_image,
    parse_recommendation_overrides_from_query,
    quality_score,
    score_image_with_time_boosts,
)
from app.core.runtime_config_cache import get_cached_runtime_config
from app.core.time import iso_utc_ms
from app.db.images_mark import mark_image_failure, mark_image_ok
from app.db.tags_get import get_tag_names_for_image
from app.db.random_pick import pick_random_image, pick_random_images
from app.db.session import create_sessionmaker
from app.jobs.enqueue import enqueue_opportunistic_hydrate_metadata

router = APIRouter()

_MAX_TAG_FILTERS = 50
_MAX_TAG_OR_TERMS = 20
_MAX_TAG_TOTAL_TERMS = 200
# Cap NOT IN size for SQLite plan quality; remaining recent ids still apply logit penalties.
_RECENT_EXCLUDE_SQL_CAP = 512

# Compatibility aliases for existing tests / internal callers.
_DEFAULT_SCORE_WEIGHTS = DEFAULT_SCORE_WEIGHTS
_DEFAULT_RECOMMENDATION = DEFAULT_RECOMMENDATION
_as_bool = as_bool
_as_float = as_float
_quality_score = quality_score
_parse_recommendation_overrides_from_query = parse_recommendation_overrides_from_query
_get_recent_sets = get_recent_sets
_record_recent = record_recent


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
    if format not in {"image", "json", "simple_json"}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported format", status_code=400)
    if redirect not in {0, 1}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported redirect", status_code=400)
    seed_norm = (seed or "").strip()
    if seed is not None and not seed_norm:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported seed", status_code=400)
    if seed_norm and len(seed_norm) > 128:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported seed", status_code=400)

    ai_type_raw = (ai_type or "any").strip().lower()
    ai_type_i: int | None = None
    if ai_type_raw in {"", "any"}:
        ai_type_i = None
    elif ai_type_raw in {"0", "1"}:
        ai_type_i = int(ai_type_raw)
    else:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported ai_type", status_code=400)

    illust_type_raw = (illust_type or "any").strip().lower()
    illust_type_i: int | None = None
    if illust_type_raw in {"", "any"}:
        illust_type_i = None
    elif illust_type_raw in {"0", "illust", "illustration"}:
        illust_type_i = 0
    elif illust_type_raw in {"1", "manga"}:
        illust_type_i = 1
    elif illust_type_raw in {"2", "ugoira"}:
        illust_type_i = 2
    else:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported illust_type", status_code=400)

    if r18 not in {0, 1, 2}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported r18", status_code=400)

    if adaptive not in {0, 1}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported adaptive", status_code=400)

    if pixiv_cat not in {0, 1}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported pixiv_cat", status_code=400)

    pximg_mirror_host_override: str | None = None
    if pximg_mirror_host is not None:
        raw = str(pximg_mirror_host or "").strip()
        if raw:
            pximg_mirror_host_override = normalize_pximg_mirror_host(raw)
            if pximg_mirror_host_override is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported pximg_mirror_host", status_code=400)

    layout_source = "orientation"
    raw_layout = orientation
    if layout is not None:
        layout_source = "layout"
        raw_layout = layout

    layout_norm = (raw_layout or "").strip().lower()
    alias_map = {"vertical": "portrait", "horizontal": "landscape"}
    layout_norm = alias_map.get(layout_norm, layout_norm)
    orientation_map = {"any": None, "portrait": 1, "landscape": 2, "square": 3}
    if layout_norm not in orientation_map:
        raise ApiError(
            code=ErrorCode.BAD_REQUEST,
            message="Unsupported layout" if layout_source == "layout" else "Unsupported orientation",
            status_code=400,
        )

    min_width_i = int(min_width)
    min_height_i = int(min_height)
    min_pixels_i = int(min_pixels)
    min_bookmarks_i = int(min_bookmarks)
    min_views_i = int(min_views)
    min_comments_i = int(min_comments)
    if min_width_i < 0 or min_height_i < 0 or min_pixels_i < 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported min_*", status_code=400)
    if min_bookmarks_i < 0 or min_views_i < 0 or min_comments_i < 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported min_*", status_code=400)

    # 自适应：在用户没有显式指定的情况下，根据设备类型设置默认的方向/分辨率门槛。
    # 注意：不会覆盖用户显式传入的 orientation/layout/min_*。
    if int(adaptive) == 1:
        qp = request.query_params
        orientation_explicit = ("layout" in qp) or ("orientation" in qp)
        min_explicit = ("min_width" in qp) or ("min_height" in qp) or ("min_pixels" in qp)

        ch_mobile = (request.headers.get("sec-ch-ua-mobile") or request.headers.get("Sec-CH-UA-Mobile") or "").strip()
        if ch_mobile == "?1":
            is_mobile = True
        elif ch_mobile == "?0":
            is_mobile = False
        else:
            ua = (request.headers.get("user-agent") or request.headers.get("User-Agent") or "").lower()
            is_mobile = any(x in ua for x in ("mobi", "android", "iphone", "ipad", "ipod"))

        if not orientation_explicit and layout_norm == "any":
            layout_norm = "portrait" if is_mobile else "landscape"

        if not min_explicit and min_width_i == 0 and min_height_i == 0 and min_pixels_i == 0:
            min_pixels_i = 1_000_000 if is_mobile else 2_000_000

    def _parse_tag_filters(values: list[str] | None) -> list[str]:
        """
        Tag filters support "AND of groups" where each query param is one group.

        Examples:
        - included_tags=girl&included_tags=boy  -> girl AND boy
        - included_tags=girl|boy               -> girl OR boy
        - included_tags=girl|boy&included_tags=white|black -> (girl OR boy) AND (white OR black)
        """
        out: list[str] = []
        seen: set[str] = set()
        for raw in values or []:
            expr = str(raw or "").strip()
            if not expr or expr in seen:
                continue
            seen.add(expr)
            out.append(expr)
        return out

    included = _parse_tag_filters(included_tags)
    excluded = _parse_tag_filters(excluded_tags)

    def _validate_tag_filters(values: list[str]) -> None:
        total_terms = 0
        for expr in values:
            parts: list[str] = []
            seen_terms: set[str] = set()
            for part in str(expr).split("|"):
                term = part.strip()
                if not term or term in seen_terms:
                    continue
                seen_terms.add(term)
                parts.append(term)
            if len(parts) > _MAX_TAG_OR_TERMS:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Too many tag terms in a group", status_code=400)
            total_terms += len(parts)
        if total_terms > _MAX_TAG_TOTAL_TERMS:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Too many tag terms", status_code=400)

    if len(included) > _MAX_TAG_FILTERS or len(excluded) > _MAX_TAG_FILTERS:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Too many tag filters", status_code=400)
    _validate_tag_filters(included)
    _validate_tag_filters(excluded)
    if user_id is not None and int(user_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported user_id", status_code=400)
    if illust_id is not None and int(illust_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported illust_id", status_code=400)

    def _normalize_iso_utc(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            raise ValueError("empty datetime")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc).replace(microsecond=0)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    created_from_norm: str | None = None
    created_to_norm: str | None = None
    try:
        if created_from is not None:
            created_from_norm = _normalize_iso_utc(created_from)
        if created_to is not None:
            created_to_norm = _normalize_iso_utc(created_to)
    except Exception:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported created_*", status_code=400)

    if created_from_norm is not None and created_to_norm is not None:
        if created_from_norm > created_to_norm:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="created_from > created_to", status_code=400)

    def _no_match_error() -> ApiError:
        applied_filters: dict[str, Any] = {
            "r18": r18,
            "r18_strict": int(r18_strict),
            "ai_type": ai_type_raw,
            "illust_type": illust_type_raw,
            "adaptive": int(adaptive),
            "orientation": layout_norm,
            "min_width": int(min_width_i),
            "min_height": int(min_height_i),
            "min_pixels": int(min_pixels_i),
            "min_bookmarks": int(min_bookmarks_i),
            "min_views": int(min_views_i),
            "min_comments": int(min_comments_i),
        }
        if included:
            applied_filters["included_tags"] = included
        if excluded:
            applied_filters["excluded_tags"] = excluded
        if user_id is not None:
            applied_filters["user_id"] = int(user_id)
        if illust_id is not None:
            applied_filters["illust_id"] = int(illust_id)
        if created_from_norm is not None:
            applied_filters["created_from"] = created_from_norm
        if created_to_norm is not None:
            applied_filters["created_to"] = created_to_norm

        suggestions: list[str] = ["运行元数据补全任务以提升元数据覆盖率"]
        if r18 == 0 and int(r18_strict) == 1:
            suggestions.append("将 r18_strict=0 以允许未知 x_restrict（冷启动阶段更容易命中）")
        if layout_norm != "any":
            suggestions.append("将 orientation=any（取消方向限制）")
        if int(min_width_i) > 0 or int(min_height_i) > 0 or int(min_pixels_i) > 0:
            suggestions.append("降低 min_width/min_height/min_pixels（放宽分辨率门槛）")
        if int(min_bookmarks_i) > 0 or int(min_views_i) > 0 or int(min_comments_i) > 0:
            suggestions.append("降低 min_bookmarks/min_views/min_comments（放宽热度门槛）")
        if included:
            suggestions.append("放宽 included_tags（减少必须包含的标签）")
        if excluded:
            suggestions.append("放宽 excluded_tags（减少必须排除的标签）")
        if user_id is not None:
            suggestions.append("移除 user_id 过滤")
        if illust_id is not None:
            suggestions.append("移除 illust_id 过滤")
        if ai_type_i is not None:
            suggestions.append("将 ai_type=any（取消 AI 限制）")
        if illust_type_i is not None:
            suggestions.append("将 illust_type=any（取消作品类型限制）")
        if created_from_norm is not None or created_to_norm is not None:
            suggestions.append("扩大 created_from/created_to 时间范围")
        if int(adaptive) == 1:
            suggestions.append("若自适应导致过滤过严，可尝试 adaptive=0 或显式设置 min_*")

        return ApiError(
            code=ErrorCode.NO_MATCH,
            message="没有匹配的图片。",
            status_code=404,
            details={"hints": {"applied_filters": applied_filters, "suggestions": suggestions}},
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

    attempts_source = "query"
    attempts_i = 3
    if attempts is not None:
        try:
            attempts_i = int(attempts)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported attempts", status_code=400) from exc
    else:
        raw = random_defaults.get("default_attempts")
        if raw is None:
            attempts_source = "fallback"
            attempts_i = 3
        else:
            attempts_source = "runtime"
            try:
                attempts_i = int(raw)
            except Exception:
                attempts_i = 3
    if attempts_i < 1 or attempts_i > 10:
        if attempts_source == "query":
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported attempts", status_code=400)
        attempts_source = "fallback"
        attempts_i = 3
    attempts = int(attempts_i)

    r18_strict_source = "query"
    r18_strict_i = 1
    if r18_strict is not None:
        try:
            r18_strict_i = int(r18_strict)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported r18_strict", status_code=400) from exc
    else:
        raw = random_defaults.get("default_r18_strict")
        if raw is None:
            r18_strict_source = "fallback"
            r18_strict_i = 1
        elif isinstance(raw, bool):
            r18_strict_source = "runtime"
            r18_strict_i = 1 if raw else 0
        else:
            r18_strict_source = "runtime"
            try:
                r18_strict_i = int(raw)
            except Exception:
                r18_strict_i = 1
    if r18_strict_i not in {0, 1}:
        if r18_strict_source == "query":
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported r18_strict", status_code=400)
        r18_strict_source = "fallback"
        r18_strict_i = 1
    r18_strict = int(r18_strict_i)

    fail_cooldown_source = "runtime"
    fail_cooldown_ms = random_defaults.get("fail_cooldown_ms")
    try:
        fail_cooldown_ms_i = int(fail_cooldown_ms) if fail_cooldown_ms is not None else None
    except Exception:
        fail_cooldown_ms_i = None

    if fail_cooldown_ms_i is None:
        fail_cooldown_source = "fallback"
        cooldown_s_raw = (os.environ.get("RANDOM_FAIL_COOLDOWN_SECONDS") or "600").strip()
        try:
            cooldown_s = int(cooldown_s_raw)
        except Exception:
            cooldown_s = 600
        cooldown_s = max(0, min(int(cooldown_s), 24 * 60 * 60))
        fail_cooldown_ms_i = int(cooldown_s) * 1000
    fail_cooldown_ms_i = max(0, min(int(fail_cooldown_ms_i), 24 * 60 * 60 * 1000))

    request_now = datetime.now(timezone.utc)
    fail_cooldown_before = (
        iso_utc_ms(request_now - timedelta(milliseconds=int(fail_cooldown_ms_i)))
        if int(fail_cooldown_ms_i) > 0
        else None
    )

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
    dedup_enabled_setting = True
    dedup_window_s = 20.0 * 60.0
    dedup_max_images = 5000
    dedup_max_authors = 2000
    dedup_strict = False
    dedup_image_penalty = 8.0
    dedup_author_penalty = 2.5
    dedup_raw = random_defaults.get("dedup")
    if isinstance(dedup_raw, dict):
        v = _as_bool(dedup_raw.get("enabled"))
        if v is not None:
            dedup_enabled_setting = bool(v)

        window_raw = dedup_raw.get("window_s")
        if window_raw is not None:
            try:
                dedup_window_s = float(max(0.0, min(float(window_raw), 24.0 * 60.0 * 60.0)))
            except Exception:
                pass

        max_images_raw = dedup_raw.get("max_images")
        if max_images_raw is not None:
            try:
                dedup_max_images = int(max(1, min(int(max_images_raw), 200_000)))
            except Exception:
                pass

        max_authors_raw = dedup_raw.get("max_authors")
        if max_authors_raw is not None:
            try:
                dedup_max_authors = int(max(1, min(int(max_authors_raw), 200_000)))
            except Exception:
                pass

        v = _as_bool(dedup_raw.get("strict"))
        if v is not None:
            dedup_strict = bool(v)

        image_pen_raw = dedup_raw.get("image_penalty")
        if image_pen_raw is not None:
            try:
                v = float(image_pen_raw)
                if math.isfinite(v):
                    dedup_image_penalty = float(max(0.0, min(v, 1000.0)))
            except Exception:
                pass

        author_pen_raw = dedup_raw.get("author_penalty")
        if author_pen_raw is not None:
            try:
                v = float(author_pen_raw)
                if math.isfinite(v):
                    dedup_author_penalty = float(max(0.0, min(v, 1000.0)))
            except Exception:
                pass

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

    strategy_raw = (strategy or "").strip().lower()
    strategy_source = "query"
    if not strategy_raw:
        strategy_source = "runtime"
        strategy_raw = str(random_defaults.get("strategy") or "").strip().lower()
    if not strategy_raw:
        strategy_source = "fallback"
        strategy_raw = "quality"
    if strategy_raw not in {"quality", "random"}:
        if strategy_source == "query":
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported strategy", status_code=400)
        strategy_source = "fallback"
        strategy_raw = "quality"

    strategy_norm = strategy_raw

    quality_samples_i: int
    quality_samples_source = "query"
    if quality_samples is not None:
        try:
            quality_samples_i = int(quality_samples)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported quality_samples", status_code=400) from exc
    else:
        raw = random_defaults.get("quality_samples")
        if raw is None:
            quality_samples_source = "fallback"
            quality_samples_i = 12
        else:
            quality_samples_source = "runtime"
            try:
                quality_samples_i = int(raw)
            except Exception:
                quality_samples_i = 12
    # Hard cap keeps latency/CPU bounded on SQLite even under strict multi-filter loads.
    # Explicit query values may still request higher (up to 200) for debugging.
    _QUALITY_SAMPLES_MAX_QUERY = 200
    _QUALITY_SAMPLES_MAX_AUTO = 64
    if quality_samples_i < 1 or quality_samples_i > _QUALITY_SAMPLES_MAX_QUERY:
        if quality_samples_source == "query":
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported quality_samples", status_code=400)
        quality_samples_source = "fallback"
        quality_samples_i = 12
    elif quality_samples_source != "query" and quality_samples_i > _QUALITY_SAMPLES_MAX_AUTO:
        quality_samples_i = _QUALITY_SAMPLES_MAX_AUTO

    quality_samples_base = int(quality_samples_i)
    quality_samples_multiplier = 1
    if quality_samples is None and strategy_norm == "quality" and bool(time_boost_enabled):
        strictness = 0
        strictness += 3 * int(len(included))
        strictness += 2 * int(len(excluded))
        if int(min_bookmarks_i) > 0:
            strictness += 2
        if int(min_views_i) > 0:
            strictness += 1
        if int(min_comments_i) > 0:
            strictness += 1
        if int(min_pixels_i) > 0:
            strictness += 1
        if int(min_width_i) > 0 or int(min_height_i) > 0:
            strictness += 1
        if ai_type_i is not None:
            strictness += 1
        if illust_type_i is not None:
            strictness += 1
        if orientation_map[layout_norm] is not None:
            strictness += 1
        if created_from_norm is not None or created_to_norm is not None:
            strictness += 1
        if int(r18) == 1:
            strictness += 1
        if bool(anti_repeat_enabled):
            strictness += 1

        if strictness >= 9:
            quality_samples_multiplier = 4
        elif strictness >= 6:
            quality_samples_multiplier = 3
        elif strictness >= 3:
            quality_samples_multiplier = 2
        else:
            quality_samples_multiplier = 1

        quality_samples_i = min(
            _QUALITY_SAMPLES_MAX_AUTO,
            int(max(1, int(quality_samples_base) * int(quality_samples_multiplier))),
        )

    quality_samples_scaled = bool(quality_samples_i != quality_samples_base)

    recommendation_raw = random_defaults.get("recommendation")
    recommendation_source = "fallback"
    recommendation_obj: dict[str, Any] = {}
    if isinstance(recommendation_raw, dict):
        recommendation_source = "runtime"
        recommendation_obj = dict(recommendation_raw)

    rec_overrides, rec_override_keys = _parse_recommendation_overrides_from_query(getattr(request, "query_params", None))
    if rec_overrides:
        recommendation_source = "query"
        # Shallow merge: keep unspecified runtime defaults, override only what user passes.
        recommendation_obj = dict(recommendation_obj)
        for k, v in rec_overrides.items():
            if k in {"score_weights", "multipliers"}:
                base_raw = recommendation_obj.get(k)
                base = dict(base_raw) if isinstance(base_raw, dict) else {}
                if isinstance(v, dict):
                    base.update(v)
                recommendation_obj[k] = base
            else:
                recommendation_obj[k] = v

    pick_mode_raw = str(recommendation_obj.get("pick_mode") or _DEFAULT_RECOMMENDATION["pick_mode"]).strip().lower()
    if pick_mode_raw not in {"best", "weighted"}:
        pick_mode_raw = str(_DEFAULT_RECOMMENDATION["pick_mode"])

    temperature_raw = _as_float(recommendation_obj.get("temperature"), default=float(_DEFAULT_RECOMMENDATION["temperature"]))
    temperature = float(max(0.05, min(float(temperature_raw), 100.0)))

    score_weights_raw = recommendation_obj.get("score_weights")
    score_weights_obj = score_weights_raw if isinstance(score_weights_raw, dict) else {}
    score_weights: dict[str, float] = {}
    for key, default_value in _DEFAULT_SCORE_WEIGHTS.items():
        v = _as_float(score_weights_obj.get(key), default=float(default_value))
        score_weights[key] = float(max(-100.0, min(float(v), 100.0)))

    multipliers_default = _DEFAULT_RECOMMENDATION["multipliers"]
    multipliers_raw = recommendation_obj.get("multipliers")
    multipliers_obj = multipliers_raw if isinstance(multipliers_raw, dict) else {}
    multipliers: dict[str, float] = {}
    for key, default_value in multipliers_default.items():
        v = _as_float(multipliers_obj.get(key), default=float(default_value))
        multipliers[key] = float(max(0.0, min(float(v), 100.0)))

    freshness_half_life_days_default = float(_DEFAULT_RECOMMENDATION["freshness_half_life_days"])
    freshness_half_life_days = freshness_half_life_days_default
    if "freshness_half_life_days" in recommendation_obj:
        v = _as_float(recommendation_obj.get("freshness_half_life_days"), default=float("nan"))
        if math.isfinite(float(v)):
            freshness_half_life_days = float(max(0.1, min(float(v), 3650.0)))

    velocity_smooth_days_default = float(_DEFAULT_RECOMMENDATION["velocity_smooth_days"])
    velocity_smooth_days = velocity_smooth_days_default
    if "velocity_smooth_days" in recommendation_obj:
        v = _as_float(recommendation_obj.get("velocity_smooth_days"), default=float("nan"))
        if math.isfinite(float(v)):
            velocity_smooth_days = float(max(0.0, min(float(v), 3650.0)))

    debug_base = {
        "attempts": int(attempts_i),
        "attempts_source": attempts_source,
        "r18_strict": int(r18_strict_i),
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
        if strategy_norm == "random":
            base_exclude = list(exclude_image_ids or [])
            exclude_set: set[int] = set(int(x) for x in base_exclude)
            if bool(anti_repeat_enabled) and recent_exclude_image_ids:
                exclude_set.update(int(x) for x in recent_exclude_image_ids)

            image = await pick_random_image(session, r=rng.random(), exclude_image_ids=list(exclude_set), **pick_kwargs)
            if image is None and bool(anti_repeat_enabled) and bool(recent_exclude_image_ids) and not bool(dedup_strict):
                image = await pick_random_image(session, r=rng.random(), exclude_image_ids=base_exclude, **pick_kwargs)
            if image is None:
                return None, {**debug_base, "attempts_used": 1, "picked_by": "random_key"}
            return image, {**debug_base, "attempts_used": 1, "picked_by": "random_key"}

        base_exclude_set: set[int] = set(int(x) for x in exclude_image_ids or [])
        exclude_set: set[int] = set(base_exclude_set)
        if bool(anti_repeat_enabled) and recent_exclude_image_ids:
            exclude_set.update(int(x) for x in recent_exclude_image_ids)
        # candidates: (image, score_total, multiplier, logit, score_base, freshness_contrib, velocity_contrib)
        candidates: list[tuple[Any, float, float, float, float, float, float]] = []

        # 批量抽样：一次性取 N 个候选（必要时 wrap-around 再取一次），避免 N 次 DB 循环查询。
        # 若用户把某些类别倍率设为 0（例如 manga=0），直接在 SQL 抽样阶段剔除，减少无效候选。
        ai_allowed: set[int | None] = set()
        if float(multipliers.get("ai", 1.0)) > 0.0:
            ai_allowed.add(1)
        if float(multipliers.get("non_ai", 1.0)) > 0.0:
            ai_allowed.add(0)
        if float(multipliers.get("unknown_ai", 1.0)) > 0.0:
            ai_allowed.add(None)

        illust_allowed: set[int | None] = set()
        if float(multipliers.get("illust", 1.0)) > 0.0:
            illust_allowed.add(0)
        if float(multipliers.get("manga", 1.0)) > 0.0:
            illust_allowed.add(1)
        if float(multipliers.get("ugoira", 1.0)) > 0.0:
            illust_allowed.add(2)
        if float(multipliers.get("unknown_illust_type", 1.0)) > 0.0:
            illust_allowed.add(None)

        if not ai_allowed or not illust_allowed:
            return None, {
                **debug_base,
                "attempts_used": 1,
                "picked_by": "quality_weighted" if pick_mode_raw == "weighted" else "quality_best",
                "candidates_drawn": 0,
                "candidates_accepted": 0,
                "quality_pick_mode": pick_mode_raw,
                "quality_temperature": float(temperature),
            }

        images = await pick_random_images(
            session,
            r=rng.random(),
            limit=int(quality_samples_i),
            exclude_image_ids=list(exclude_set),
            ai_type_allowed=ai_allowed,
            illust_type_allowed=illust_allowed,
            **pick_kwargs,
        )
        if not images and bool(anti_repeat_enabled) and bool(recent_exclude_image_ids) and not bool(dedup_strict):
            images = await pick_random_images(
                session,
                r=rng.random(),
                limit=int(quality_samples_i),
                exclude_image_ids=list(base_exclude_set),
                ai_type_allowed=ai_allowed,
                illust_type_allowed=illust_allowed,
                **pick_kwargs,
            )

        drawn = int(len(images))
        accepted = 0
        for image in images:
            score, score_base, freshness_contrib, velocity_contrib = score_image_with_time_boosts(
                image,
                score_weights=score_weights,
                freshness_half_life_days=float(freshness_half_life_days),
                velocity_smooth_days=float(velocity_smooth_days),
                time_boost_enabled=bool(time_boost_enabled),
            )
            multiplier = multiplier_for_image(image, multipliers=multipliers)
            if multiplier <= 0.0:
                continue

            logit = float(score) / float(temperature) + math.log(float(multiplier))
            if bool(anti_repeat_enabled):
                try:
                    if int(getattr(image, "id", 0) or 0) in recent_image_ids:
                        logit -= float(dedup_image_penalty)
                except Exception:
                    pass
                try:
                    uid = getattr(image, "user_id", None)
                    if uid is not None and int(uid) in recent_author_ids:
                        logit -= float(dedup_author_penalty)
                except Exception:
                    pass
            candidates.append(
                (
                    image,
                    float(score),
                    float(multiplier),
                    float(logit),
                    float(score_base),
                    float(freshness_contrib),
                    float(velocity_contrib),
                )
            )
            accepted += 1

        if not candidates:
            return None, {
                **debug_base,
                "attempts_used": 1,
                "picked_by": "quality_weighted" if pick_mode_raw == "weighted" else "quality_best",
                "candidates_drawn": int(drawn),
                "candidates_accepted": int(accepted),
                "quality_pick_mode": pick_mode_raw,
                "quality_temperature": float(temperature),
            }

        if pick_mode_raw == "best":
            picked = max(candidates, key=lambda x: x[3])
            picked_by = "quality_best"
        else:
            max_logit = max(x[3] for x in candidates)
            weights = [math.exp(float(x[3]) - float(max_logit)) for x in candidates]
            total = float(sum(weights))
            if not math.isfinite(total) or total <= 0.0:
                picked = max(candidates, key=lambda x: x[3])
                picked_by = "quality_best"
            else:
                r = float(rng.random()) * total
                idx = 0
                for i, w in enumerate(weights):
                    r -= float(w)
                    if r <= 0:
                        idx = i
                        break
                picked = candidates[int(max(0, min(idx, len(candidates) - 1)))]
                picked_by = "quality_weighted"

        best_image, best_score, best_multiplier, _best_logit, best_base, best_fresh, best_vel = picked

        return (
            best_image,
            {
                **debug_base,
                "attempts_used": 1,
                "picked_by": picked_by,
                "candidates_drawn": int(drawn),
                "candidates_accepted": int(accepted),
                "quality_pick_mode": pick_mode_raw,
                "quality_temperature": float(temperature),
                "quality_score": float(best_score),
                "quality_score_base": float(best_base),
                "quality_score_freshness": float(best_fresh),
                "quality_score_bookmark_velocity": float(best_vel),
                "quality_multiplier": float(best_multiplier),
            },
        )

    def _needs_opportunistic_hydrate(image: Any) -> bool:
        return (
            getattr(image, "width", None) is None
            or getattr(image, "height", None) is None
            or getattr(image, "x_restrict", None) is None
            or getattr(image, "ai_type", None) is None
            or getattr(image, "illust_type", None) is None
            or getattr(image, "user_id", None) is None
            or getattr(image, "bookmark_count", None) is None
            or getattr(image, "view_count", None) is None
            or getattr(image, "comment_count", None) is None
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
                _record_recent(
                    now=time.monotonic(),
                    image_id=int(image.id),
                    user_id=int(image.user_id) if getattr(image, "user_id", None) is not None else None,
                    window_s=float(dedup_window_s),
                    max_images=int(dedup_max_images),
                    max_authors=int(dedup_max_authors),
                )
            except Exception:
                pass

        if _needs_opportunistic_hydrate(image):
            background_tasks.add_task(
                _best_effort,
                enqueue_opportunistic_hydrate_metadata,
                engine,
                illust_id=int(image.illust_id),
                reason="random",
                timeout_s=2.5,
            )

        if format == "image" and redirect == 1:
            qp: list[tuple[str, str]] = []
            if proxy_override is not None:
                qp.append(("proxy", str(proxy_override)))
            else:
                if int(pixiv_cat) == 1:
                    qp.append(("pixiv_cat", "1"))
                if pximg_mirror_host_override is not None:
                    qp.append(("pximg_mirror_host", str(pximg_mirror_host_override)))
            qs = ("?" + "&".join([f"{k}={v}" for k, v in qp])) if qp else ""
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

        if format == "simple_json":
            return {
                "ok": True,
                "code": "OK",
                "request_id": getattr(getattr(request, "state", None), "request_id", None) or "req_unknown",
                "data": {
                    "image": {
                        "id": str(image.id),
                        "illust_id": str(image.illust_id),
                        "page_index": image.page_index,
                        "ext": image.ext,
                        "width": image.width,
                        "height": image.height,
                        "x_restrict": image.x_restrict,
                        "ai_type": image.ai_type,
                        "illust_type": getattr(image, "illust_type", None),
                        "bookmark_count": getattr(image, "bookmark_count", None),
                        "view_count": getattr(image, "view_count", None),
                        "comment_count": getattr(image, "comment_count", None),
                        "user": {
                            "id": str(image.user_id) if image.user_id is not None else None,
                            "name": image.user_name,
                        },
                    },
                    "urls": {
                        "proxy": resolve_public_proxy_url(
                            settings=request.app.state.settings,
                            original_url=str(image.original_url),
                            local_proxy_path=f"/i/{image.id}.{image.ext}",
                        ),
                        "origin": origin_url,
                        "imgproxy": imgproxy_url,
                    },
                    "debug": {
                        **debug,
                    },
                },
            }

        return {
            "ok": True,
            "code": "OK",
            "request_id": getattr(getattr(request, "state", None), "request_id", None) or "req_unknown",
            "data": {
                "image": {
                    "id": str(image.id),
                    "illust_id": str(image.illust_id),
                    "page_index": image.page_index,
                    "ext": image.ext,
                    "width": image.width,
                    "height": image.height,
                    "x_restrict": image.x_restrict,
                    "ai_type": image.ai_type,
                    "illust_type": getattr(image, "illust_type", None),
                    "bookmark_count": getattr(image, "bookmark_count", None),
                    "view_count": getattr(image, "view_count", None),
                    "comment_count": getattr(image, "comment_count", None),
                    "user": {
                        "id": str(image.user_id) if image.user_id is not None else None,
                        "name": image.user_name,
                    },
                    "title": image.title,
                    "created_at_pixiv": image.created_at_pixiv,
                },
                "tags": tags,
                "urls": {
                    "proxy": resolve_public_proxy_url(
                        settings=request.app.state.settings,
                        original_url=str(image.original_url),
                        local_proxy_path=f"/i/{image.id}.{image.ext}",
                    ),
                    "origin": origin_url,
                    "imgproxy": imgproxy_url,
                    "legacy_single": f"/{image.illust_id}.{image.ext}",
                    "legacy_multi": f"/{image.illust_id}-{image.page_index + 1}.{image.ext}",
                },
                "debug": {
                    **debug,
                },
            },
        }

    tried_ids: set[int] = set()
    last_error: ApiError | None = None
    attempts_i = int(attempts)
    runtime_stream = runtime

    for _ in range(attempts_i):
        async with Session() as session:
            image, _debug = await _pick_with_strategy(session=session, exclude_image_ids=list(tried_ids))
            if image is None:
                break
            image_id = int(image.id)
            origin_url = str(image.original_url)
            source_url = rewrite_pximg_to_mirror(origin_url, mirror_host=mirror_host) if use_pixiv_cat else origin_url
            illust_id_for_hydrate = int(image.illust_id)
            needs_hydrate = _needs_opportunistic_hydrate(image)
            should_mark_ok = image.last_ok_at is None or image.last_error_code is not None
            user_id_for_recent = int(image.user_id) if getattr(image, "user_id", None) is not None else None

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
                    _record_recent(
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
