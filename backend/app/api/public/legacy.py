from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.errors import ApiError, ErrorCode
from app.core.http_stream import stream_url
from app.core.pixiv_urls import ALLOWED_IMAGE_EXTS
from app.core.pximg_reverse_proxy import (
    normalize_pximg_mirror_host,
    normalize_pximg_proxy,
    pick_pximg_mirror_host_for_request,
    rewrite_pximg_to_mirror,
)
from app.core.proxy_routing import select_proxy_uri_for_url
from app.core.runtime_settings import load_runtime_config
from app.db.images_get_by_illust import get_image_by_illust_page
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
    if int(illust_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported illust_id", status_code=400)
    if int(page) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported page", status_code=400)

    ext = (ext or "").lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported ext", status_code=400)
    if pixiv_cat not in {0, 1}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported pixiv_cat", status_code=400)

    pximg_mirror_host_override: str | None = None
    if pximg_mirror_host is not None:
        raw = str(pximg_mirror_host or "").strip()
        if raw:
            pximg_mirror_host_override = normalize_pximg_mirror_host(raw)
            if pximg_mirror_host_override is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported pximg_mirror_host", status_code=400)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        image = await get_image_by_illust_page(session, illust_id=illust_id, page_index=int(page) - 1)
        if image is None or (image.ext or "").lower() != ext:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)

    runtime = await load_runtime_config(engine)

    proxy_override: str | None = None
    if proxy is not None:
        raw = str(proxy or "").strip()
        if raw:
            proxy_override = normalize_pximg_proxy(raw, extra_hosts=runtime.image_proxy_extra_pximg_mirror_hosts)
            if proxy_override is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported proxy", status_code=400)

    mirror_host_override = proxy_override or pximg_mirror_host_override
    use_pixiv_cat = bool(runtime.image_proxy_use_pixiv_cat) or int(pixiv_cat) == 1 or proxy_override is not None
    runtime_mirror_host = str(getattr(runtime, "image_proxy_pximg_mirror_host", "") or "").strip() or "i.pixiv.cat"
    mirror_host = mirror_host_override or (
        pick_pximg_mirror_host_for_request(headers=request.headers, fallback_host=runtime_mirror_host)
        if use_pixiv_cat
        else runtime_mirror_host
    )
    proxy_uri = None
    source_url = rewrite_pximg_to_mirror(str(image.original_url), mirror_host=mirror_host) if use_pixiv_cat else str(image.original_url)
    if not use_pixiv_cat:
        picked = await select_proxy_uri_for_url(
            engine,
            request.app.state.settings,
            runtime,
            url=str(image.original_url),
        )
        if picked is not None:
            proxy_uri = picked.uri

    transport = getattr(request.app.state, "httpx_transport", None)
    return await stream_url(
        source_url,
        transport=transport,
        proxy=proxy_uri,
        cache_control="public, max-age=31536000, immutable",
        range_header=request.headers.get("Range"),
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
    if pixiv_cat not in {0, 1}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported pixiv_cat", status_code=400)

    pximg_mirror_host_override: str | None = None
    if pximg_mirror_host is not None:
        raw = str(pximg_mirror_host or "").strip()
        if raw:
            pximg_mirror_host_override = normalize_pximg_mirror_host(raw)
            if pximg_mirror_host_override is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported pximg_mirror_host", status_code=400)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async with Session() as session:
        image = await get_image_by_illust_page(session, illust_id=illust_id, page_index=0)
        if image is None or (image.ext or "").lower() != ext:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)

    runtime = await load_runtime_config(engine)

    proxy_override: str | None = None
    if proxy is not None:
        raw = str(proxy or "").strip()
        if raw:
            proxy_override = normalize_pximg_proxy(raw, extra_hosts=runtime.image_proxy_extra_pximg_mirror_hosts)
            if proxy_override is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported proxy", status_code=400)

    mirror_host_override = proxy_override or pximg_mirror_host_override
    use_pixiv_cat = bool(runtime.image_proxy_use_pixiv_cat) or int(pixiv_cat) == 1 or proxy_override is not None
    runtime_mirror_host = str(getattr(runtime, "image_proxy_pximg_mirror_host", "") or "").strip() or "i.pixiv.cat"
    mirror_host = mirror_host_override or (
        pick_pximg_mirror_host_for_request(headers=request.headers, fallback_host=runtime_mirror_host)
        if use_pixiv_cat
        else runtime_mirror_host
    )
    proxy_uri = None
    source_url = rewrite_pximg_to_mirror(str(image.original_url), mirror_host=mirror_host) if use_pixiv_cat else str(image.original_url)
    if not use_pixiv_cat:
        picked = await select_proxy_uri_for_url(
            engine,
            request.app.state.settings,
            runtime,
            url=str(image.original_url),
        )
        if picked is not None:
            proxy_uri = picked.uri

    transport = getattr(request.app.state, "httpx_transport", None)
    return await stream_url(
        source_url,
        transport=transport,
        proxy=proxy_uri,
        cache_control="public, max-age=31536000, immutable",
        range_header=request.headers.get("Range"),
    )
