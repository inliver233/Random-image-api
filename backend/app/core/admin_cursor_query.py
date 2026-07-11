from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.errors import ApiError, ErrorCode


@dataclass(frozen=True, slots=True)
class ParsedAdminIntCursor:
    """Shared limit + positive-int cursor for admin list endpoints."""

    limit: int
    cursor_i: int | None


def parse_admin_int_cursor(
    *,
    limit: int,
    cursor: str | None,
    limit_max: int = 200,
) -> ParsedAdminIntCursor:
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

    return ParsedAdminIntCursor(limit=int(limit), cursor_i=cursor_i)


def slice_id_cursor_page(rows: list[Any], limit: int) -> tuple[list[Any], int | None]:
    """
    Split id-desc list query results into one page + next cursor id.

    Callers query with limit+1; page is rows[:limit], next_cursor is last page id
    when an overflow row exists.
    """
    page = list(rows[: int(limit)])
    next_cursor = int(page[-1].id) if len(rows) > int(limit) and page else None
    return page, next_cursor

