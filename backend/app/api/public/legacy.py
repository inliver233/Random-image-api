from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.admin_request import require_positive_id
from app.core.errors import ApiError, ErrorCode
from app.core.image_delivery import deliver_public_image_from_request, normalize_image_ext
from app.core.random_delivery import resolve_catalog_store
from app.db.session import create_sessionmaker

router = APIRouter()


@router.get("/{illust_id}-{page}.{ext}")
async def legacy_multi(
    request: Request,
    illust_id: int,
    page: int,
    ext: str,
    pixiv_cat: int = 0,
    pximg_mirror_host: str | None = None,
    proxy: str | None = None,
):
    illust_id = require_positive_id(illust_id, invalid_message="Unsupported illust_id")
    page = require_positive_id(page, invalid_message="Unsupported page")
    ext = normalize_image_ext(ext)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    catalog = resolve_catalog_store(getattr(request.app.state, "catalog_store", None))

    async with Session() as session:
        image = await catalog.get_image_by_illust_page(
            session, illust_id=illust_id, page_index=int(page) - 1
        )
        if image is None or (image.ext or "").lower() != ext:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)

    return await deliver_public_image_from_request(
        request=request,
        image=image,
        pixiv_cat=pixiv_cat,
        pximg_mirror_host=pximg_mirror_host,
        proxy=proxy,
        background_tasks=None,
        needs_hydrate=False,
        should_mark_ok=False,
        mark_fail_on_upstream=False,
    )


@router.get("/{illust_id}.{ext}")
async def legacy_single(
    request: Request,
    illust_id: int,
    ext: str,
    pixiv_cat: int = 0,
    pximg_mirror_host: str | None = None,
    proxy: str | None = None,
):
    illust_id = require_positive_id(illust_id, invalid_message="Unsupported illust_id")
    ext = normalize_image_ext(ext)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    catalog = resolve_catalog_store(getattr(request.app.state, "catalog_store", None))

    async with Session() as session:
        image = await catalog.get_image_by_illust_page(session, illust_id=illust_id, page_index=0)
        if image is None or (image.ext or "").lower() != ext:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)

    return await deliver_public_image_from_request(
        request=request,
        image=image,
        pixiv_cat=pixiv_cat,
        pximg_mirror_host=pximg_mirror_host,
        proxy=proxy,
        background_tasks=None,
        needs_hydrate=False,
        should_mark_ok=False,
        mark_fail_on_upstream=False,
    )
