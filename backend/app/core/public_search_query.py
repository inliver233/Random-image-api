from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.admin_cursor_query import parse_admin_int_cursor
from app.core.errors import ApiError, ErrorCode


@dataclass(frozen=True, slots=True)
class ParsedPublicSearchQuery:
    """Shared q/limit/cursor shape for public search lists (authors, tags)."""

    q: str | None
    limit: int
    # Authors use positive integer cursors; tags use opaque name cursors.
    cursor_i: int | None
    cursor_s: str | None


def parse_public_search_query(
    *,
    q: str | None,
    limit: int,
    cursor: str | None,
    limit_max: int = 100,
    q_max_len: int = 200,
    cursor_kind: Literal["int", "str"] = "int",
) -> ParsedPublicSearchQuery:
    q_norm = (q or "").strip()
    if q_norm and len(q_norm) > int(q_max_len):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported q", status_code=400)

    cursor_i: int | None = None
    cursor_s: str | None = None

    if cursor_kind == "int":
        # Same limit/cursor contract as admin/public list int cursors.
        parsed = parse_admin_int_cursor(limit=limit, cursor=cursor, limit_max=limit_max)
        limit = parsed.limit
        cursor_i = parsed.cursor_i
    else:
        if limit < 1 or limit > int(limit_max):
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported limit", status_code=400)
        # Tag name cursors: empty => no cursor; non-empty opaque string passthrough.
        cursor_s = (cursor or "").strip() or None

    return ParsedPublicSearchQuery(
        q=q_norm or None,
        limit=int(limit),
        cursor_i=cursor_i,
        cursor_s=cursor_s,
    )
