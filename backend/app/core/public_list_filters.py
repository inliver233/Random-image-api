from __future__ import annotations

from dataclasses import dataclass

from app.core.admin_cursor_query import parse_admin_int_cursor
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

ORIENTATION_ALIASES: dict[str, str] = {
    "vertical": "portrait",
    "horizontal": "landscape",
}


def parse_ai_type_param(ai_type: str) -> tuple[str, int | None]:
    """Parse public ai_type query token → (normalized_raw, code). Unsupported → BAD_REQUEST."""
    ai_type_raw = (ai_type or "any").strip().lower()
    if ai_type_raw in {"", "any"}:
        return ai_type_raw, None
    if ai_type_raw in {"0", "1"}:
        return ai_type_raw, int(ai_type_raw)
    raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported ai_type", status_code=400)


def parse_orientation_param(
    value: str,
    *,
    aliases: dict[str, str] | None = None,
    invalid_message: str = "Unsupported orientation",
) -> tuple[str, int | None]:
    """Parse orientation/layout token → (normalized_name, code)."""
    layout_norm = (value or "").strip().lower()
    if aliases:
        layout_norm = aliases.get(layout_norm, layout_norm)
    if layout_norm not in ORIENTATION_MAP:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid_message, status_code=400)
    return layout_norm, ORIENTATION_MAP[layout_norm]


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
    # Same limit/cursor contract as admin int-cursor lists (messages preserved).
    parsed_cursor = parse_admin_int_cursor(limit=limit, cursor=cursor, limit_max=limit_max)
    limit = parsed_cursor.limit
    cursor_i = parsed_cursor.cursor_i

    if r18 not in {0, 1, 2}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported r18", status_code=400)
    if r18_strict not in {0, 1}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported r18_strict", status_code=400)

    _, ai_type_i = parse_ai_type_param(ai_type)
    orientation_norm, orientation_code = parse_orientation_param(orientation)

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
        orientation_code=orientation_code,
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
