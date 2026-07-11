from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import JSONResponse

from app.core.errors import ApiError, ErrorCode
from app.core.image_delivery import deliver_known_image, needs_image_proxy_hydrate, should_mark_image_ok
from app.core.pixiv_urls import ALLOWED_IMAGE_EXTS
from app.core.proxy_mirror import resolve_proxy_mirror
from app.core.random_query import (
    MAX_TAG_FILTERS,
    normalize_iso_utc,
    parse_tag_filters,
    validate_tag_filters,
)
from app.core.request_id import get_or_create_request_id, set_request_id_header, set_request_id_on_state
from app.core.runtime_config_cache import get_cached_runtime_config
from app.db.images_get import get_image_by_id
from app.db.images_list import list_images as db_list_images
from app.db.session import create_sessionmaker
from app.db.tags_get import get_tag_names_for_image

router = APIRouter()


@router.get("/images")
async def list_images(
    request: Request,
    limit: int = 50,
    cursor: str | None = None,
    r18: int = 0,
    r18_strict: int = 1,
    ai_type: str = "any",
    orientation: str = "any",
    min_width: int = 0,
    min_height: int = 0,
    min_pixels: int = 0,
    included_tags: list[str] | None = Query(default=None),
    excluded_tags: list[str] | None = Query(default=None),
    user_id: int | None = None,
    illust_id: int | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> Any:
    if limit < 1 or limit > 200:
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

    orientation = (orientation or "").strip().lower()
    orientation_map = {"any": None, "portrait": 1, "landscape": 2, "square": 3}
    if orientation not in orientation_map:
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

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    async with Session() as session:
        images, next_cursor = await db_list_images(
            session,
            limit=limit,
            cursor=cursor_i,
            r18=r18,
            r18_strict=bool(r18_strict),
            orientation=orientation_map[orientation],
            ai_type=ai_type_i,
            min_width=min_width,
            min_height=min_height,
            min_pixels=min_pixels,
            included_tags=included,
            excluded_tags=excluded,
            user_id=user_id,
            illust_id=illust_id,
            created_from=created_from_norm,
            created_to=created_to_norm,
        )

    rid = get_or_create_request_id(request)
    set_request_id_on_state(request, rid)

    items = [
        {
            "id": str(img.id),
            "illust_id": str(img.illust_id),
            "page_index": img.page_index,
            "ext": img.ext,
            "width": img.width,
            "height": img.height,
            "x_restrict": img.x_restrict,
            "ai_type": img.ai_type,
            "bookmark_count": getattr(img, "bookmark_count", None),
            "view_count": getattr(img, "view_count", None),
            "comment_count": getattr(img, "comment_count", None),
            "user": {
                "id": str(img.user_id) if img.user_id is not None else None,
                "name": img.user_name,
            },
            "title": img.title,
            "created_at_pixiv": img.created_at_pixiv,
        }
        for img in images
    ]

    resp = JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "items": items,
            "next_cursor": str(next_cursor) if next_cursor is not None else "",
            "request_id": rid,
        },
    )
    set_request_id_header(resp, rid)
    return resp


@router.get("/images/{image_id}")
async def get_image(
    request: Request,
    image_id: int,
) -> Any:
    if int(image_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported image_id", status_code=400)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        image = await get_image_by_id(session, image_id=image_id)
        if image is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)
        tags = await get_tag_names_for_image(session, image_id=image.id)

    rid = get_or_create_request_id(request)
    set_request_id_on_state(request, rid)

    resp = JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "item": {
                "image": {
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
                    "title": image.title,
                    "created_at_pixiv": image.created_at_pixiv,
                },
                "tags": tags,
            },
            "request_id": rid,
        },
    )
    set_request_id_header(resp, rid)
    return resp


@router.get("/i/{image_id}.{ext}")
async def proxy_image(
    request: Request,
    image_id: int,
    ext: str,
    background_tasks: BackgroundTasks,
    pixiv_cat: int = 0,
    pximg_mirror_host: str | None = None,
    proxy: str | None = None,
):
    ext = (ext or "").lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported ext", status_code=400)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        image = await get_image_by_id(session, image_id=image_id)
        if image is None or (image.ext or "").lower() != ext:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)
        should_mark_ok = should_mark_image_ok(image)
        needs_hydrate = await needs_image_proxy_hydrate(session, image)

    cache = getattr(request.app.state, "runtime_config_cache", None)
    if cache is not None:
        runtime = await cache.get(engine)
    else:
        runtime = await get_cached_runtime_config(engine)
    resolved = resolve_proxy_mirror(
        runtime=runtime,
        headers=request.headers,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host=pximg_mirror_host,
        proxy=proxy,
    )
    force_local = str(request.query_params.get("local") or "").strip().lower() in {"1", "true", "yes"}

    return await deliver_known_image(
        request=request,
        engine=engine,
        settings=request.app.state.settings,
        runtime=runtime,
        image=image,
        background_tasks=background_tasks,
        proxy_override=resolved.proxy_override,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host_override=resolved.pximg_mirror_host_override,
        force_local=force_local,
        use_pixiv_cat=resolved.use_pixiv_cat,
        needs_hydrate=bool(needs_hydrate),
        should_mark_ok=bool(should_mark_ok),
        hydrate_reason="image_proxy",
        mark_fail_on_upstream=True,
    )
