from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import Query
from fastapi.responses import RedirectResponse

from app.core.errors import ApiError, ErrorCode
from app.core.public_list_filters import (
    ORIENTATION_ALIASES,
    ORIENTATION_MAP,
    parse_ai_type_param,
    parse_orientation_param,
)
from app.core.pximg_reverse_proxy import normalize_pximg_mirror_host
from app.core.random_query import (
    parse_created_range,
    parse_included_excluded_tags,
    require_optional_positive_ids,
)


@dataclass(frozen=True)
class ParsedRandomFilters:
    format: str
    redirect: int
    seed_norm: str
    ai_type_raw: str
    ai_type_i: int | None
    illust_type_raw: str
    illust_type_i: int | None
    r18: int
    adaptive: int
    pixiv_cat: int
    pximg_mirror_host_override: str | None
    layout_norm: str
    orientation_map: dict[str, int | None]
    min_width_i: int
    min_height_i: int
    min_pixels_i: int
    min_bookmarks_i: int
    min_views_i: int
    min_comments_i: int
    included: list[str]
    excluded: list[str]
    user_id: int | None
    illust_id: int | None
    created_from_norm: str | None
    created_to_norm: str | None


class PublicRandomQuery:
    """Shared FastAPI query dependency for /random and /feed filter fields.

    Endpoint-specific knobs stay on the route (format/redirect/attempts vs limit).
    """

    def __init__(
        self,
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
    ) -> None:
        self.seed = seed
        self.strategy = strategy
        self.quality_samples = quality_samples
        self.r18 = r18
        self.r18_strict = r18_strict
        self.ai_type = ai_type
        self.illust_type = illust_type
        self.orientation = orientation
        self.layout = layout
        self.adaptive = adaptive
        self.pixiv_cat = pixiv_cat
        self.pximg_mirror_host = pximg_mirror_host
        self.proxy = proxy
        self.min_width = min_width
        self.min_height = min_height
        self.min_pixels = min_pixels
        self.min_bookmarks = min_bookmarks
        self.min_views = min_views
        self.min_comments = min_comments
        self.included_tags = included_tags
        self.excluded_tags = excluded_tags
        self.user_id = user_id
        self.illust_id = illust_id
        self.created_from = created_from
        self.created_to = created_to

    def parse_filters(
        self,
        *,
        format: str,
        redirect: int,
        query_params: Mapping[str, Any] | Any | None,
        headers: Mapping[str, str] | Any | None,
    ) -> ParsedRandomFilters:
        return parse_random_filters(
            format=format,
            redirect=redirect,
            seed=self.seed,
            r18=self.r18,
            ai_type=self.ai_type,
            illust_type=self.illust_type,
            orientation=self.orientation,
            layout=self.layout,
            adaptive=self.adaptive,
            pixiv_cat=self.pixiv_cat,
            pximg_mirror_host=self.pximg_mirror_host,
            min_width=self.min_width,
            min_height=self.min_height,
            min_pixels=self.min_pixels,
            min_bookmarks=self.min_bookmarks,
            min_views=self.min_views,
            min_comments=self.min_comments,
            included_tags=self.included_tags,
            excluded_tags=self.excluded_tags,
            user_id=self.user_id,
            illust_id=self.illust_id,
            created_from=self.created_from,
            created_to=self.created_to,
            query_params=query_params,
            headers=headers,
        )


def _detect_mobile(headers: Mapping[str, str] | Any) -> bool:
    def _h(name: str) -> str:
        try:
            return str(headers.get(name) or headers.get(name.title()) or "").strip()
        except Exception:
            return ""

    ch_mobile = _h("sec-ch-ua-mobile") or _h("Sec-CH-UA-Mobile")
    if ch_mobile == "?1":
        return True
    if ch_mobile == "?0":
        return False
    ua = (_h("user-agent") or _h("User-Agent")).lower()
    return any(x in ua for x in ("mobi", "android", "iphone", "ipad", "ipod"))


def parse_random_filters(
    *,
    format: str,
    redirect: int,
    seed: str | None,
    r18: int,
    ai_type: str,
    illust_type: str,
    orientation: str,
    layout: str | None,
    adaptive: int,
    pixiv_cat: int,
    pximg_mirror_host: str | None,
    min_width: int,
    min_height: int,
    min_pixels: int,
    min_bookmarks: int,
    min_views: int,
    min_comments: int,
    included_tags: list[str] | None,
    excluded_tags: list[str] | None,
    user_id: int | None,
    illust_id: int | None,
    created_from: str | None,
    created_to: str | None,
    query_params: Mapping[str, Any] | Any | None = None,
    headers: Mapping[str, str] | Any | None = None,
) -> ParsedRandomFilters:
    if format not in {"image", "json", "simple_json"}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported format", status_code=400)
    if redirect not in {0, 1}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported redirect", status_code=400)

    seed_norm = (seed or "").strip()
    if seed is not None and not seed_norm:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported seed", status_code=400)
    if seed_norm and len(seed_norm) > 128:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported seed", status_code=400)

    ai_type_raw, ai_type_i = parse_ai_type_param(ai_type)

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

    layout_norm, _orientation_code = parse_orientation_param(
        str(raw_layout or ""),
        aliases=ORIENTATION_ALIASES,
        invalid_message="Unsupported layout" if layout_source == "layout" else "Unsupported orientation",
    )
    orientation_map = dict(ORIENTATION_MAP)

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

    # Adaptive defaults: only fill when user did not pass orientation/layout/min_*.
    if int(adaptive) == 1:
        qp = query_params or {}
        try:
            orientation_explicit = ("layout" in qp) or ("orientation" in qp)
            min_explicit = ("min_width" in qp) or ("min_height" in qp) or ("min_pixels" in qp)
        except Exception:
            orientation_explicit = False
            min_explicit = False
        is_mobile = _detect_mobile(headers or {})
        if not orientation_explicit and layout_norm == "any":
            layout_norm = "portrait" if is_mobile else "landscape"
        if not min_explicit and min_width_i == 0 and min_height_i == 0 and min_pixels_i == 0:
            min_pixels_i = 1_000_000 if is_mobile else 2_000_000

    included, excluded = parse_included_excluded_tags(included_tags, excluded_tags)
    user_id_i, illust_id_i = require_optional_positive_ids(user_id=user_id, illust_id=illust_id)
    created_from_norm, created_to_norm = parse_created_range(created_from, created_to)

    return ParsedRandomFilters(
        format=format,
        redirect=int(redirect),
        seed_norm=seed_norm,
        ai_type_raw=ai_type_raw,
        ai_type_i=ai_type_i,
        illust_type_raw=illust_type_raw,
        illust_type_i=illust_type_i,
        r18=int(r18),
        adaptive=int(adaptive),
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host_override=pximg_mirror_host_override,
        layout_norm=layout_norm,
        orientation_map=orientation_map,
        min_width_i=int(min_width_i),
        min_height_i=int(min_height_i),
        min_pixels_i=int(min_pixels_i),
        min_bookmarks_i=int(min_bookmarks_i),
        min_views_i=int(min_views_i),
        min_comments_i=int(min_comments_i),
        included=included,
        excluded=excluded,
        user_id=user_id_i,
        illust_id=illust_id_i,
        created_from_norm=created_from_norm,
        created_to_norm=created_to_norm,
    )


def force_local_from_query(query_params: Any) -> bool:
    """True when client forces local stream (skip CF image edge entirely)."""
    return str(query_params.get("local") or "").strip().lower() in {"1", "true", "yes"}


def prefer_edge_browser_redirect_from_query(query_params: Any) -> bool:
    """True when client opts into browser 302 to signed workers.dev URL.

    H0: default delivery is same-origin 200 + BFF stream via img-worker.
    Only ``redirect=1`` / ``edge_redirect=1`` (or true/yes/on) enable browser 302.
    """
    if query_params is None:
        return False
    for key in ("edge_redirect", "redirect"):
        raw = query_params.get(key)
        if raw is None:
            continue
        try:
            if int(raw) == 1:
                return True
        except Exception:
            pass
        if str(raw or "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def parse_public_debug_flag(query_params: Any) -> bool:
    """True when client requests debug payload on public JSON (?debug=1). Default off."""
    raw = query_params.get("debug") if query_params is not None else None
    try:
        return int(raw or 0) == 1
    except Exception:
        return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def prefer_image_edge(
    *,
    proxy_override: str | None,
    pixiv_cat: int,
    pximg_mirror_host_override: str | None,
    force_local: bool = False,
) -> bool:
    """True when public delivery should prefer CF image edge over local/mirror.

    When ready, default product path is same-origin **stream** via signed
    img-worker (H0). Browser 302 is a separate opt-in (redirect=1).
    """
    if force_local:
        return False
    if proxy_override is not None:
        return False
    if int(pixiv_cat) == 1:
        return False
    if pximg_mirror_host_override is not None:
        return False
    return True


def local_i_query_string(
    *,
    proxy_override: str | None,
    pixiv_cat: int,
    pximg_mirror_host_override: str | None,
) -> str:
    qp: list[tuple[str, str]] = []
    if proxy_override is not None:
        qp.append(("proxy", str(proxy_override)))
    else:
        if int(pixiv_cat) == 1:
            qp.append(("pixiv_cat", "1"))
        if pximg_mirror_host_override is not None:
            qp.append(("pximg_mirror_host", str(pximg_mirror_host_override)))
    return ("?" + "&".join([f"{k}={v}" for k, v in qp])) if qp else ""


def build_local_i_redirect_response(
    *,
    image_id: int,
    ext: str,
    proxy_override: str | None = None,
    pixiv_cat: int = 0,
    pximg_mirror_host_override: str | None = None,
    cache_control: str = "no-store",
) -> RedirectResponse:
    """302 to local /i/{id}.{ext} with optional proxy/mirror query flags."""
    qs = local_i_query_string(
        proxy_override=proxy_override,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host_override=pximg_mirror_host_override,
    )
    return RedirectResponse(
        url=f"/i/{int(image_id)}.{ext}{qs}",
        status_code=302,
        headers={"Cache-Control": cache_control},
    )
