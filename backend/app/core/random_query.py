from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.errors import ApiError, ErrorCode

MAX_TAG_FILTERS = 50
MAX_TAG_OR_TERMS = 20
MAX_TAG_TOTAL_TERMS = 200


def parse_tag_filters(values: list[str] | None) -> list[str]:
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


def validate_tag_filters(values: list[str]) -> None:
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
        if len(parts) > MAX_TAG_OR_TERMS:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Too many tag terms in a group", status_code=400)
        total_terms += len(parts)
    if total_terms > MAX_TAG_TOTAL_TERMS:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Too many tag terms", status_code=400)


def normalize_iso_utc(value: str) -> str:
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


def normalize_iso_utc_optional(value: str | None) -> str | None:
    """Soft ISO UTC seconds normalizer; empty → None (invalid still raises)."""
    raw = (value or "").strip()
    if not raw:
        return None
    return normalize_iso_utc(raw)


def parse_included_excluded_tags(
    included_tags: list[str] | None,
    excluded_tags: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Parse + validate public included/excluded tag groups (shared list/random contract)."""
    included = parse_tag_filters(included_tags)
    excluded = parse_tag_filters(excluded_tags)
    if len(included) > MAX_TAG_FILTERS or len(excluded) > MAX_TAG_FILTERS:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Too many tag filters", status_code=400)
    validate_tag_filters(included)
    validate_tag_filters(excluded)
    return included, excluded


def parse_created_range(
    created_from: str | None,
    created_to: str | None,
) -> tuple[str | None, str | None]:
    """Normalize optional created_from/created_to ISO range; invalid → BAD_REQUEST."""
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
    return created_from_norm, created_to_norm


def require_optional_positive_ids(
    *,
    user_id: int | None,
    illust_id: int | None,
) -> tuple[int | None, int | None]:
    """Optional positive user_id/illust_id with stable Unsupported messages."""
    if user_id is not None and int(user_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported user_id", status_code=400)
    if illust_id is not None and int(illust_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported illust_id", status_code=400)
    return (int(user_id) if user_id is not None else None, int(illust_id) if illust_id is not None else None)


def no_match_error_from_filters(filters: Any, *, r18_strict: int) -> ApiError:
    """Build NO_MATCH from ParsedRandomFilters (shared by /random and /feed)."""
    return build_no_match_error(
        r18=int(filters.r18),
        r18_strict=int(r18_strict),
        ai_type_raw=filters.ai_type_raw,
        illust_type_raw=filters.illust_type_raw,
        adaptive=int(filters.adaptive),
        layout_norm=filters.layout_norm,
        min_width_i=int(filters.min_width_i),
        min_height_i=int(filters.min_height_i),
        min_pixels_i=int(filters.min_pixels_i),
        min_bookmarks_i=int(filters.min_bookmarks_i),
        min_views_i=int(filters.min_views_i),
        min_comments_i=int(filters.min_comments_i),
        included=list(filters.included),
        excluded=list(filters.excluded),
        user_id=filters.user_id,
        illust_id=filters.illust_id,
        created_from_norm=filters.created_from_norm,
        created_to_norm=filters.created_to_norm,
        ai_type_i=filters.ai_type_i,
        illust_type_i=filters.illust_type_i,
    )


def build_no_match_error(
    *,
    r18: int,
    r18_strict: int,
    ai_type_raw: str,
    illust_type_raw: str,
    adaptive: int,
    layout_norm: str,
    min_width_i: int,
    min_height_i: int,
    min_pixels_i: int,
    min_bookmarks_i: int,
    min_views_i: int,
    min_comments_i: int,
    included: list[str],
    excluded: list[str],
    user_id: int | None,
    illust_id: int | None,
    created_from_norm: str | None,
    created_to_norm: str | None,
    ai_type_i: int | None,
    illust_type_i: int | None,
) -> ApiError:
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
