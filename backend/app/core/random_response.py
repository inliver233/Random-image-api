from __future__ import annotations

from typing import Any

from app.core.public_json import serialize_public_image_core


def build_public_urls_block(
    *,
    proxy_url: str,
    origin_url: str | None,
    imgproxy_url: str | None,
    local_url: str,
    include_legacy: bool = False,
    illust_id: Any = None,
    page_index: Any = None,
    ext: Any = None,
) -> dict[str, Any]:
    """Shared urls object for /random JSON and /feed items."""
    urls: dict[str, Any] = {
        "proxy": proxy_url,
        # Always-present origin-side stream path for client edge→local cascade.
        "local": local_url,
        "origin": origin_url,
        "imgproxy": imgproxy_url,
    }
    if include_legacy and illust_id is not None and page_index is not None and ext is not None:
        urls["legacy_single"] = f"/{illust_id}.{ext}"
        urls["legacy_multi"] = f"/{illust_id}-{int(page_index) + 1}.{ext}"
    return urls


def build_simple_item_payload(
    *,
    image: Any,
    proxy_url: str,
    origin_url: str | None,
    imgproxy_url: str | None,
    debug: dict[str, Any] | None = None,
    local_url: str | None = None,
) -> dict[str, Any]:
    """One feed/simple_json item body (without outer ok envelope)."""
    local = local_url or f"/i/{image.id}.{image.ext}"
    payload: dict[str, Any] = {
        "image": serialize_public_image_core(image, include_illust_type=True),
        "urls": build_public_urls_block(
            proxy_url=proxy_url,
            origin_url=origin_url,
            imgproxy_url=imgproxy_url,
            local_url=local,
        ),
    }
    if debug is not None:
        payload["debug"] = {**debug}
    return payload


def build_simple_json_body(
    *,
    request_id: str,
    image: Any,
    proxy_url: str,
    origin_url: str | None,
    imgproxy_url: str | None,
    debug: dict[str, Any] | None = None,
    local_url: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "code": "OK",
        "request_id": request_id,
        "data": build_simple_item_payload(
            image=image,
            proxy_url=proxy_url,
            origin_url=origin_url,
            imgproxy_url=imgproxy_url,
            debug=debug,
            local_url=local_url,
        ),
    }


def build_json_body(
    *,
    request_id: str,
    image: Any,
    tags: list[str],
    proxy_url: str,
    origin_url: str | None,
    imgproxy_url: str | None,
    debug: dict[str, Any] | None = None,
    local_url: str | None = None,
) -> dict[str, Any]:
    image_obj = serialize_public_image_core(
        image,
        include_illust_type=True,
        include_title=True,
        include_created_at=True,
    )
    local = local_url or f"/i/{image.id}.{image.ext}"
    data: dict[str, Any] = {
        "image": image_obj,
        "tags": tags,
        "urls": build_public_urls_block(
            proxy_url=proxy_url,
            origin_url=origin_url,
            imgproxy_url=imgproxy_url,
            local_url=local,
            include_legacy=True,
            illust_id=image.illust_id,
            page_index=image.page_index,
            ext=image.ext,
        ),
    }
    if debug is not None:
        data["debug"] = {**debug}
    return {
        "ok": True,
        "code": "OK",
        "request_id": request_id,
        "data": data,
    }


def build_feed_json_body(
    *,
    request_id: str,
    items: list[dict[str, Any]],
    requested: int,
) -> dict[str, Any]:
    """Batch envelope for GET /feed (simple_json item shape per entry)."""
    return {
        "ok": True,
        "code": "OK",
        "request_id": request_id,
        "data": {
            "items": items,
            "count": len(items),
            "requested": int(requested),
        },
    }
