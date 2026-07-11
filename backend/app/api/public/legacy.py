from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.errors import ApiError, ErrorCode
from app.core.image_delivery import deliver_known_image
from app.core.pixiv_urls import ALLOWED_IMAGE_EXTS
from app.core.proxy_mirror import resolve_proxy_mirror
from app.core.runtime_config_cache import get_cached_runtime_config
from app.db.images_get_by_illust import get_image_by_illust_page
from app.db.session import create_sessionmaker

router = APIRouter()


async def _resolve_runtime(request: Request, engine):
    cache = getattr(request.app.state, "runtime_config_cache", None)
    if cache is not None:
        return await cache.get(engine)
    return await get_cached_runtime_config(engine)


async def _deliver_legacy_image(
    *,
    request: Request,
    image,
    pixiv_cat: int,
    pximg_mirror_host: str | None,
    proxy: str | None,
):
    engine = request.app.state.engine
    runtime = await _resolve_runtime(request, engine)
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
        background_tasks=None,
        proxy_override=resolved.proxy_override,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host_override=resolved.pximg_mirror_host_override,
        force_local=force_local,
        use_pixiv_cat=resolved.use_pixiv_cat,
        needs_hydrate=False,
        should_mark_ok=False,
        mark_fail_on_upstream=False,
    )


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
    if int(illust_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported illust_id", status_code=400)
    if int(page) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported page", status_code=400)

    ext = (ext or "").lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported ext", status_code=400)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        image = await get_image_by_illust_page(session, illust_id=illust_id, page_index=int(page) - 1)
        if image is None or (image.ext or "").lower() != ext:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)

    return await _deliver_legacy_image(
        request=request,
        image=image,
        pixiv_cat=pixiv_cat,
        pximg_mirror_host=pximg_mirror_host,
        proxy=proxy,
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
    if int(illust_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported illust_id", status_code=400)

    ext = (ext or "").lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported ext", status_code=400)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        image = await get_image_by_illust_page(session, illust_id=illust_id, page_index=0)
        if image is None or (image.ext or "").lower() != ext:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)

    return await _deliver_legacy_image(
        request=request,
        image=image,
        pixiv_cat=pixiv_cat,
        pximg_mirror_host=pximg_mirror_host,
        proxy=proxy,
    )
