from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from app.core.request_id import (
    get_or_create_request_id,
    set_request_id_header,
    set_request_id_on_state,
)


def public_ok_json(
    request: Any,
    *,
    payload: dict[str, Any] | None = None,
    status_code: int = 200,
) -> JSONResponse:
    """JSON envelope with ok + request_id + X-Request-Id header."""
    rid = get_or_create_request_id(request)
    set_request_id_on_state(request, rid)
    body: dict[str, Any] = {"ok": True, "request_id": rid}
    if payload:
        body.update(payload)
    resp = JSONResponse(status_code=int(status_code), content=body)
    set_request_id_header(resp, rid)
    return resp


def public_cursor_list_json(
    request: Any,
    *,
    items: list[Any],
    next_cursor: Any,
) -> JSONResponse:
    cursor_out = ""
    if next_cursor is not None and str(next_cursor) != "":
        cursor_out = str(next_cursor)
    return public_ok_json(
        request,
        payload={
            "items": items,
            "next_cursor": cursor_out,
        },
    )


def serialize_public_image_core(
    image: Any,
    *,
    include_illust_type: bool = False,
    include_title: bool = False,
    include_created_at: bool = False,
) -> dict[str, Any]:
    """Shared public image fields used by list/detail and /random JSON."""
    payload: dict[str, Any] = {
        "id": str(image.id),
        "illust_id": str(image.illust_id),
        "page_index": image.page_index,
        "ext": image.ext,
        "width": image.width,
        "height": image.height,
        "x_restrict": image.x_restrict,
        "ai_type": image.ai_type,
        "bookmark_count": getattr(image, "bookmark_count", None),
        "view_count": getattr(image, "view_count", None),
        "comment_count": getattr(image, "comment_count", None),
        "user": {
            "id": str(image.user_id) if image.user_id is not None else None,
            "name": image.user_name,
        },
    }
    if include_illust_type:
        payload["illust_type"] = getattr(image, "illust_type", None)
    if include_title:
        payload["title"] = image.title
    if include_created_at:
        payload["created_at_pixiv"] = image.created_at_pixiv
    return payload


def serialize_public_image(image: Any) -> dict[str, Any]:
    """Stable public image object for list/detail JSON."""
    return serialize_public_image_core(
        image,
        include_title=True,
        include_created_at=True,
    )
