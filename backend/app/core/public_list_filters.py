from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import ApiError, ErrorCode
from app.core.random_query import (
    MAX_TAG_FILTERS,
    normalize_iso_utc,
    parse_tag_filters,
    validate_tag_filters,
)

ORIENTATION_MAP: dict[str, int | None] = {
    "any": None,
    "portrait": 1,
    "landscape": 2,
    "square": 3,
}


@dataclass(frozen=True, slots=True)
class ParsedPublicListFilters:
    """Shared filter shape for public cursor lists (e.g. GET /images)."""

    r18: int
    r18_strict: int
    ai_type_i: int | None
    orientation_code: int | None
    min_width_i: int
    min_height_i: int
    min_pixels_i: int
    included: list[str]
    excluded: list[str]
    user_id: int | None
    illust_id: int | None
    created_from_norm: str | None
    created_to_norm: str | None
    cursor_i: int | None
    limit: int


def parse_public_list_filters(
    *,
    limit: int,
    cursor: str | None,
    r18: int,
    r18_strict: int,
    ai_type: str,
    orientation: str,
    min_width: int,
    min_height: int,
    min_pixels: int,
    included_tags: list[str] | None,
    excluded_tags: list[str] | None,
    user_id: int | None,
    illust_id: int | None,
    created_from: str | None,
    created_to: str | None,
    limit_max: int = 200,
) -> ParsedPublicListFilters:
    if limit < 1 or limit > int(limit_max):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported limit", status_code=400)

    cursor_i: int | None = None
    cursor_raw = (cursor or "").strip()
    if cursor_raw:
        if not cursor_raw.isdigit():
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported cursor", status_code=400)
        cursor_i = int(cursor_raw)
        if cursor_i <= 0:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported cursor", status_code=400)

    if r18 not in {0, 1, 2}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported r18", status_code=400)
    if r18_strict not in {0, 1}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported r18_strict", status_code=400)

    ai_type_raw = (ai_type or "any").strip().lower()
    ai_type_i: int | None = None
    if ai_type_raw in {"", "any"}:
        ai_type_i = None
    elif ai_type_raw in {"0", "1"}:
        ai_type_i = int(ai_type_raw)
    else:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported ai_type", status_code=400)

    orientation_norm = (orientation or "").strip().lower()
    if orientation_norm not in ORIENTATION_MAP:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported orientation", status_code=400)

    if min_width < 0 or min_height < 0 or min_pixels < 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported min_*", status_code=400)

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

    return ParsedPublicListFilters(
        r18=int(r18),
        r18_strict=int(r18_strict),
        ai_type_i=ai_type_i,
        orientation_code=ORIENTATION_MAP[orientation_norm],
        min_width_i=int(min_width),
        min_height_i=int(min_height),
        min_pixels_i=int(min_pixels),
        included=included,
        excluded=excluded,
        user_id=int(user_id) if user_id is not None else None,
        illust_id=int(illust_id) if illust_id is not None else None,
        created_from_norm=created_from_norm,
        created_to_norm=created_to_norm,
        cursor_i=cursor_i,
        limit=int(limit),
    )
