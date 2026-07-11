from __future__ import annotations

from typing import Any

from app.core.request_id import get_or_create_request_id


def admin_ok(
    request: Any,
    *,
    payload: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Admin JSON envelope: ok + request_id (+ optional payload fields)."""
    rid = request_id if request_id is not None else get_or_create_request_id(request)
    body: dict[str, Any] = {"ok": True, "request_id": rid}
    if payload:
        body.update(payload)
    return body


def admin_cursor_list(
    request: Any,
    *,
    items: list[Any],
    next_cursor: Any,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Admin cursor list envelope: ok + items + next_cursor + request_id."""
    cursor_out = ""
    if next_cursor is not None and str(next_cursor) != "":
        cursor_out = str(next_cursor)
    payload: dict[str, Any] = {
        "items": items,
        "next_cursor": cursor_out,
    }
    if extra:
        payload.update(extra)
    return admin_ok(request, payload=payload, request_id=request_id)
