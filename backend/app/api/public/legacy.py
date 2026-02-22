from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.errors import ApiError, ErrorCode
from app.core.http_stream import stream_url
from app.core.pixiv_urls import ALLOWED_IMAGE_EXTS
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

    runtime = await load_runtime_config(engine)
    proxy_uri = None
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
        image.original_url,
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

    runtime = await load_runtime_config(engine)
    proxy_uri = None
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
        image.original_url,
        transport=transport,
        proxy=proxy_uri,
        cache_control="public, max-age=31536000, immutable",
        range_header=request.headers.get("Range"),
    )
