from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.core.errors import ApiError, ErrorCode
from app.core.pximg_reverse_proxy import normalize_pximg_mirror_host
from app.core.random_query import (
    MAX_TAG_FILTERS,
    normalize_iso_utc,
    parse_tag_filters,
    validate_tag_filters,
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
    orientation_map: dict[str, int | None] = {"any": None, "portrait": 1, "landscape": 2, "square": 3}
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

    included = parse_tag_filters(included_tags)
    excluded = parse_tag_filters(excluded_tags)
    if len(included) > MAX_TAG_FILTERS or len(excluded) > MAX_TAG_FILTERS:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Too many tag filters", status_code=400)
    validate_tag_filters(included)
    validate_tag_filters(excluded)

    if user_id is not None and int(user_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported user_id", status_code=400)
    if illust_id is not None and int(illust_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported illust_id", status_code=400)

    created_from_norm: str | None = None
    created_to_norm: str | None = None
    try:
        if created_from is not None:
            created_from_norm = normalize_iso_utc(created_from)
        if created_to is not None:
            created_to_norm = normalize_iso_utc(created_to)
    except Exception:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported created_*", status_code=400)

    if created_from_norm is not None and created_to_norm is not None:
        if created_from_norm > created_to_norm:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="created_from > created_to", status_code=400)

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
        user_id=int(user_id) if user_id is not None else None,
        illust_id=int(illust_id) if illust_id is not None else None,
        created_from_norm=created_from_norm,
        created_to_norm=created_to_norm,
    )


def prefer_image_edge(
    *,
    proxy_override: str | None,
    pixiv_cat: int,
    pximg_mirror_host_override: str | None,
    force_local: bool = False,
) -> bool:
    """True when public delivery should prefer CF signed edge over local stream/mirror."""
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
