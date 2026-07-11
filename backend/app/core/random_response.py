from __future__ import annotations

from typing import Any

from app.core.public_json import serialize_public_image_core


def build_simple_json_body(
    *,
    request_id: str,
    image: Any,
    proxy_url: str,
    origin_url: str | None,
    imgproxy_url: str | None,
    debug: dict[str, Any],
    local_url: str | None = None,
) -> dict[str, Any]:
    local = local_url or f"/i/{image.id}.{image.ext}"
    return {
        "ok": True,
        "code": "OK",
        "request_id": request_id,
        "data": {
            "image": serialize_public_image_core(image, include_illust_type=True),
            "urls": {
                "proxy": proxy_url,
                # Always-present origin-side stream path for client edge→local cascade.
                "local": local,
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
    local_url: str | None = None,
) -> dict[str, Any]:
    image_obj = serialize_public_image_core(
        image,
        include_illust_type=True,
        include_title=True,
        include_created_at=True,
    )
    local = local_url or f"/i/{image.id}.{image.ext}"
    return {
        "ok": True,
        "code": "OK",
        "request_id": request_id,
        "data": {
            "image": image_obj,
            "tags": tags,
            "urls": {
                "proxy": proxy_url,
                "local": local,
                "origin": origin_url,
                "imgproxy": imgproxy_url,
                "legacy_single": f"/{image.illust_id}.{image.ext}",
                "legacy_multi": f"/{image.illust_id}-{image.page_index + 1}.{image.ext}",
            },
            "debug": {**debug},
        },
    }
