from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request

from app.core.admin_request import require_positive_id
from app.core.errors import ApiError, ErrorCode
from app.core.image_delivery import deliver_known_image, needs_image_proxy_hydrate, should_mark_image_ok
from app.core.pixiv_urls import ALLOWED_IMAGE_EXTS
from app.core.proxy_mirror import resolve_proxy_mirror
from app.core.public_json import public_cursor_list_json, public_ok_json, serialize_public_image
from app.core.public_list_filters import parse_public_list_filters
from app.core.random_request import force_local_from_query
from app.core.runtime_config_cache import resolve_runtime_for_request
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
    filters = parse_public_list_filters(
        limit=limit,
        cursor=cursor,
        r18=r18,
        r18_strict=r18_strict,
        ai_type=ai_type,
        orientation=orientation,
        min_width=min_width,
        min_height=min_height,
        min_pixels=min_pixels,
        included_tags=included_tags,
        excluded_tags=excluded_tags,
        user_id=user_id,
        illust_id=illust_id,
        created_from=created_from,
        created_to=created_to,
    )

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    async with Session() as session:
        images, next_cursor = await db_list_images(
            session,
            limit=filters.limit,
            cursor=filters.cursor_i,
            r18=filters.r18,
            r18_strict=bool(filters.r18_strict),
            orientation=filters.orientation_code,
            ai_type=filters.ai_type_i,
            min_width=filters.min_width_i,
            min_height=filters.min_height_i,
            min_pixels=filters.min_pixels_i,
            included_tags=filters.included,
            excluded_tags=filters.excluded,
            user_id=filters.user_id,
            illust_id=filters.illust_id,
            created_from=filters.created_from_norm,
            created_to=filters.created_to_norm,
        )

    return public_cursor_list_json(
        request,
        items=[serialize_public_image(img) for img in images],
        next_cursor=next_cursor,
    )


@router.get("/images/{image_id}")
async def get_image(
    request: Request,
    image_id: int,
) -> Any:
    image_id = require_positive_id(image_id, invalid_message="Unsupported image_id")

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        image = await get_image_by_id(session, image_id=image_id)
        if image is None:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)
        tags = await get_tag_names_for_image(session, image_id=image.id)

    return public_ok_json(
        request,
        payload={
            "item": {
                "image": serialize_public_image(image),
                "tags": tags,
            },
        },
    )


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

    runtime = await resolve_runtime_for_request(request, engine)
    resolved = resolve_proxy_mirror(
        runtime=runtime,
        headers=request.headers,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host=pximg_mirror_host,
        proxy=proxy,
    )
    force_local = force_local_from_query(request.query_params)

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
