from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.errors import ApiError, ErrorCode
from app.core.image_delivery import deliver_known_image
from app.core.pixiv_urls import ALLOWED_IMAGE_EXTS
from app.core.pximg_reverse_proxy import (
    normalize_pximg_mirror_host,
    normalize_pximg_proxy,
)
from app.core.runtime_config_cache import get_cached_runtime_config
from app.db.images_get_by_illust import get_image_by_illust_page
from app.db.session import create_sessionmaker

router = APIRouter()


async def _resolve_runtime(request: Request, engine):
    cache = getattr(request.app.state, "runtime_config_cache", None)
    if cache is not None:
        return await cache.get(engine)
    return await get_cached_runtime_config(engine)


def _parse_proxy_overrides(
    *,
    runtime,
    pixiv_cat: int,
    pximg_mirror_host: str | None,
    proxy: str | None,
) -> tuple[str | None, str | None, bool]:
    """Return (proxy_override, pximg_mirror_host_override, use_pixiv_cat)."""
    if pixiv_cat not in {0, 1}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported pixiv_cat", status_code=400)

    pximg_mirror_host_override: str | None = None
    if pximg_mirror_host is not None:
        raw = str(pximg_mirror_host or "").strip()
        if raw:
            pximg_mirror_host_override = normalize_pximg_mirror_host(raw)
            if pximg_mirror_host_override is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported pximg_mirror_host", status_code=400)

    proxy_override: str | None = None
    if proxy is not None:
        raw = str(proxy or "").strip()
        if raw:
            proxy_override = normalize_pximg_proxy(raw, extra_hosts=runtime.image_proxy_extra_pximg_mirror_hosts)
            if proxy_override is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported proxy", status_code=400)

    use_pixiv_cat = bool(runtime.image_proxy_use_pixiv_cat) or int(pixiv_cat) == 1 or proxy_override is not None
    return proxy_override, pximg_mirror_host_override, use_pixiv_cat


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
    proxy_override, pximg_mirror_host_override, use_pixiv_cat = _parse_proxy_overrides(
        runtime=runtime,
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
        proxy_override=proxy_override,
        pixiv_cat=int(pixiv_cat),
        pximg_mirror_host_override=pximg_mirror_host_override,
        force_local=force_local,
        use_pixiv_cat=use_pixiv_cat,
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
