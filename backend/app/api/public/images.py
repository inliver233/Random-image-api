from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import JSONResponse

from app.core.errors import ApiError, ErrorCode
from app.core.http_stream import stream_url
import sqlalchemy as sa

from app.core.pixiv_urls import ALLOWED_IMAGE_EXTS
from app.core.pximg_reverse_proxy import (
    normalize_pximg_mirror_host,
    pick_pximg_mirror_host_for_request,
    rewrite_pximg_to_mirror,
)
from app.core.request_id import get_or_create_request_id, set_request_id_header, set_request_id_on_state
from app.core.runtime_settings import load_runtime_config
from app.core.time import iso_utc_ms
from app.core.proxy_routing import select_proxy_uri_for_url
from app.db.images_get import get_image_by_id
from app.db.images_list import list_images as db_list_images
from app.db.images_mark import mark_image_failure, mark_image_ok
from app.db.models.image_tags import ImageTag
from app.db.session import create_sessionmaker
from app.db.tags_get import get_tag_names_for_image
from app.jobs.enqueue import enqueue_opportunistic_hydrate_metadata

router = APIRouter()

_MAX_TAG_FILTERS = 50
_MAX_TAG_OR_TERMS = 20
_MAX_TAG_TOTAL_TERMS = 200


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

    def _parse_tag_filters(values: list[str] | None) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in values or []:
            expr = str(raw or "").strip()
            if not expr or expr in seen:
                continue
            seen.add(expr)
            out.append(expr)
        return out

    def _validate_tag_filters(values: list[str]) -> None:
        total_terms = 0
        for expr in values:
            parts: list[str] = []
            seen_terms: set[str] = set()
            for part in str(expr).split("|"):
                term = part.strip()
                if not term or term in seen_terms:
                    continue
                seen_terms.add(term)
                parts.append(term)
            if len(parts) > _MAX_TAG_OR_TERMS:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Too many tag terms in a group", status_code=400)
            total_terms += len(parts)
        if total_terms > _MAX_TAG_TOTAL_TERMS:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Too many tag terms", status_code=400)

    included = _parse_tag_filters(included_tags)
    excluded = _parse_tag_filters(excluded_tags)
    if len(included) > _MAX_TAG_FILTERS or len(excluded) > _MAX_TAG_FILTERS:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Too many tag filters", status_code=400)
    _validate_tag_filters(included)
    _validate_tag_filters(excluded)

    if user_id is not None and int(user_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported user_id", status_code=400)
    if illust_id is not None and int(illust_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported illust_id", status_code=400)

    def _normalize_iso_utc(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            raise ValueError("empty datetime")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc).replace(microsecond=0)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    created_from_norm: str | None = None
    created_to_norm: str | None = None
    try:
        if created_from is not None:
            created_from_norm = _normalize_iso_utc(created_from)
        if created_to is not None:
            created_to_norm = _normalize_iso_utc(created_to)
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
):
    ext = (ext or "").lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported ext", status_code=400)
    if pixiv_cat not in {0, 1}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported pixiv_cat", status_code=400)

    mirror_host_override: str | None = None
    if pximg_mirror_host is not None:
        raw = str(pximg_mirror_host or "").strip()
        if raw:
            mirror_host_override = normalize_pximg_mirror_host(raw)
            if mirror_host_override is None:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported pximg_mirror_host", status_code=400)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    needs_hydrate = False
    should_mark_ok = False
    async with Session() as session:
        image = await get_image_by_id(session, image_id=image_id)
        if image is None or (image.ext or "").lower() != ext:
            raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)
        should_mark_ok = image.last_ok_at is None or image.last_error_code is not None

        missing_basic_fields = (
            getattr(image, "width", None) is None
            or getattr(image, "height", None) is None
            or getattr(image, "x_restrict", None) is None
            or getattr(image, "ai_type", None) is None
            or getattr(image, "user_id", None) is None
            or not str(getattr(image, "user_name", "") or "").strip()
            or not str(getattr(image, "title", "") or "").strip()
            or not str(getattr(image, "created_at_pixiv", "") or "").strip()
            or getattr(image, "bookmark_count", None) is None
            or getattr(image, "view_count", None) is None
            or getattr(image, "comment_count", None) is None
        )
        if missing_basic_fields:
            needs_hydrate = True
        else:
            tag_row = (
                await session.execute(
                    sa.select(ImageTag.image_id).where(ImageTag.image_id == int(image.id)).limit(1)
                )
            ).scalar_one_or_none()
            needs_hydrate = tag_row is None

    runtime = await load_runtime_config(engine)
    use_pixiv_cat = bool(runtime.image_proxy_use_pixiv_cat) or int(pixiv_cat) == 1
    runtime_mirror_host = str(getattr(runtime, "image_proxy_pximg_mirror_host", "") or "").strip() or "i.pixiv.cat"
    mirror_host = mirror_host_override or (
        pick_pximg_mirror_host_for_request(headers=request.headers, fallback_host=runtime_mirror_host)
        if use_pixiv_cat
        else runtime_mirror_host
    )

    async def _best_effort(fn, *args, timeout_s: float = 1.5, **kwargs) -> None:  # type: ignore[no-untyped-def]
        try:
            await asyncio.wait_for(fn(*args, **kwargs), timeout=float(timeout_s))
        except Exception:
            pass

    source_url = rewrite_pximg_to_mirror(str(image.original_url), mirror_host=mirror_host) if use_pixiv_cat else str(image.original_url)
    proxy_uri = None
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
    now = iso_utc_ms()
    try:
        resp = await stream_url(
            source_url,
            transport=transport,
            proxy=proxy_uri,
            cache_control="public, max-age=31536000, immutable",
            range_header=request.headers.get("Range"),
        )
        if should_mark_ok:
            background_tasks.add_task(_best_effort, mark_image_ok, engine, image_id=int(image.id), now=now, timeout_s=1.5)
        if needs_hydrate:
            background_tasks.add_task(
                _best_effort,
                enqueue_opportunistic_hydrate_metadata,
                engine,
                illust_id=int(image.illust_id),
                reason="image_proxy",
                timeout_s=2.5,
            )
        try:
            if getattr(resp, "background", None) is None:
                resp.background = background_tasks
        except Exception:
            pass
        return resp
    except ApiError as exc:
        if exc.code in {
            ErrorCode.UPSTREAM_STREAM_ERROR,
            ErrorCode.UPSTREAM_403,
            ErrorCode.UPSTREAM_404,
            ErrorCode.UPSTREAM_RATE_LIMIT,
        }:
            await _best_effort(
                mark_image_failure,
                engine,
                image_id=int(image.id),
                now=now,
                error_code=exc.code.value,
                error_message=exc.message,
                timeout_s=1.5,
            )
        raise
