from __future__ import annotations

from typing import Any


def _image_core_fields(image: Any) -> dict[str, Any]:
    return {
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
    }


def build_simple_json_body(
    *,
    request_id: str,
    image: Any,
    proxy_url: str,
    origin_url: str | None,
    imgproxy_url: str | None,
    debug: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "code": "OK",
        "request_id": request_id,
        "data": {
            "image": _image_core_fields(image),
            "urls": {
                "proxy": proxy_url,
                "origin": origin_url,
                "imgproxy": imgproxy_url,
            },
            "debug": {**debug},
        },
    }


def build_json_body(
    *,
    request_id: str,
    image: Any,
    tags: list[str],
    proxy_url: str,
    origin_url: str | None,
    imgproxy_url: str | None,
    debug: dict[str, Any],
) -> dict[str, Any]:
    image_obj = _image_core_fields(image)
    image_obj["title"] = image.title
    image_obj["created_at_pixiv"] = image.created_at_pixiv
    return {
        "ok": True,
        "code": "OK",
        "request_id": request_id,
        "data": {
            "image": image_obj,
            "tags": tags,
            "urls": {
                "proxy": proxy_url,
                "origin": origin_url,
                "imgproxy": imgproxy_url,
                "legacy_single": f"/{image.illust_id}.{image.ext}",
                "legacy_multi": f"/{image.illust_id}-{image.page_index + 1}.{image.ext}",
            },
            "debug": {**debug},
        },
    }
