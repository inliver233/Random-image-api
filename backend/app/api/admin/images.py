from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.admin.deps import get_admin_claims
from app.core.admin_cursor_query import parse_admin_int_cursor
from app.core.admin_json import admin_cursor_list, admin_ok
from app.core.admin_request import load_json_object, parse_bool, parse_choice, parse_positive_int_list, require_positive_id
from app.core.errors import ApiError, ErrorCode
from app.core.random_delivery import resolve_catalog_store
from app.core.random_engine_sync import maybe_publish_engine_deletes, maybe_publish_engine_empty_snapshot
from app.core.request_id import get_or_create_request_id
from app.db.session import create_sessionmaker, with_sqlite_busy_retry
from app.db.tag_store import resolve_tag_store

router = APIRouter()

_ALLOWED_MISSING = frozenset({"tags", "geometry", "r18", "ai", "illust_type", "user", "title", "created_at", "popularity"})


def _parse_missing(values: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        for part in str(raw or "").replace(",", "|").split("|"):
            text = str(part or "").strip()
            if not text:
                continue
            key = parse_choice(text, field="missing", choices=_ALLOWED_MISSING, invalid_message="Unsupported missing")
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


@router.get("/images")
async def list_admin_images(
    request: Request,
    limit: int = 50,
    cursor: str | None = None,
    missing: list[str] | None = Query(default=None),
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    parsed = parse_admin_int_cursor(limit=limit, cursor=cursor, limit_max=200)
    limit = parsed.limit
    cursor_i = parsed.cursor_i

    missing_keys = _parse_missing(missing)

    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    catalog = resolve_catalog_store(getattr(request.app.state, "catalog_store", None))

    async with Session() as session:
        rows_page, next_cursor = await catalog.list_admin_images(
            session,
            limit=int(limit),
            cursor=cursor_i,
            missing_keys=missing_keys,
        )

    items: list[dict[str, Any]] = []
    for img, tag_count in rows_page:
        tag_count_i = int(tag_count or 0)
        missing_list: list[str] = []
        if tag_count_i <= 0:
            missing_list.append("tags")
        if img.width is None or img.height is None:
            missing_list.append("geometry")
        if img.x_restrict is None:
            missing_list.append("r18")
        if img.ai_type is None:
            missing_list.append("ai")
        if getattr(img, "illust_type", None) is None:
            missing_list.append("illust_type")
        if img.user_id is None:
            missing_list.append("user")
        if img.title is None or not str(img.title).strip():
            missing_list.append("title")
        if img.created_at_pixiv is None or not str(img.created_at_pixiv).strip():
            missing_list.append("created_at")
        if img.bookmark_count is None or img.view_count is None or img.comment_count is None:
            missing_list.append("popularity")

        items.append(
            {
                "id": str(img.id),
                "illust_id": str(img.illust_id),
                "page_index": int(img.page_index),
                "ext": img.ext,
                "status": int(img.status),
                "width": img.width,
                "height": img.height,
                "orientation": img.orientation,
                "x_restrict": img.x_restrict,
                "ai_type": img.ai_type,
                "illust_type": getattr(img, "illust_type", None),
                "bookmark_count": img.bookmark_count,
                "view_count": img.view_count,
                "comment_count": img.comment_count,
                "user": {
                    "id": str(img.user_id) if img.user_id is not None else None,
                    "name": img.user_name,
                },
                "title": img.title,
                "created_at_pixiv": img.created_at_pixiv,
                "original_url": img.original_url,
                "proxy_path": img.proxy_path,
                "tag_count": tag_count_i,
                "missing": missing_list,
            }
        )

    return admin_cursor_list(request, items=items, next_cursor=next_cursor, request_id=rid)


async def _load_bulk_delete_json(request: Request) -> dict[str, Any]:
    data = await load_json_object(request)

    raw_ids = data.get("image_ids", None)
    if raw_ids is None:
        raw_ids = data.get("ids", None)
    if raw_ids is None:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing image_ids", status_code=400)

    ids = parse_positive_int_list(raw_ids, field="image_ids", max_items=20_000)
    return {"image_ids": ids}


@router.delete("/images/{image_id}")
async def delete_admin_image(
    image_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    image_id = require_positive_id(image_id, invalid_message="Invalid image id")

    rid = get_or_create_request_id(request)
    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    catalog = resolve_catalog_store(getattr(request.app.state, "catalog_store", None))
    tags = resolve_tag_store(getattr(request.app.state, "tag_store", None))

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            # Tags via TagStore; clear links before image rows.
            await tags.delete_image_tags_for_image_ids(session, image_ids=[int(image_id)])
            deleted_ids = await catalog.delete_images_by_ids(session, image_ids=[int(image_id)])
            if not deleted_ids:
                raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)
            await session.commit()

        return admin_ok(request, payload={"image_id": str(int(image_id))}, request_id=rid)

    result = await with_sqlite_busy_retry(_op)
    await maybe_publish_engine_deletes(image_ids=[int(image_id)], client=getattr(request.app.state, "httpx_client", None))
    return result


@router.post("/images/bulk-delete")
async def bulk_delete_admin_images(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    body = await _load_bulk_delete_json(request)
    ids = list(body["image_ids"])

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    catalog = resolve_catalog_store(getattr(request.app.state, "catalog_store", None))
    tags = resolve_tag_store(getattr(request.app.state, "tag_store", None))

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            # Tags via TagStore; clear links before image rows.
            await tags.delete_image_tags_for_image_ids(session, image_ids=list(ids))
            found_ids = await catalog.delete_images_by_ids(session, image_ids=list(ids))
            await session.commit()

        missing = max(0, int(len(ids)) - int(len(found_ids)))
        return {
            "response": admin_ok(
                request,
                payload={
                    "requested": int(len(ids)),
                    "deleted": int(len(found_ids)),
                    "missing": int(missing),
                },
                request_id=rid,
            ),
            "deleted_ids": list(found_ids),
        }

    out = await with_sqlite_busy_retry(_op)
    deleted_ids = list(out.get("deleted_ids") or [])
    if deleted_ids:
        await maybe_publish_engine_deletes(
            image_ids=deleted_ids,
            client=getattr(request.app.state, "httpx_client", None),
        )
    return out["response"]


async def _load_clear_images_json(request: Request) -> dict[str, Any]:
    data = await load_json_object(request)

    if not parse_bool(data.get("confirm"), default=False):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing confirm", status_code=400)

    return {"delete_tags": parse_bool(data.get("delete_tags"), default=True)}


@router.post("/images/clear")
async def clear_admin_images(
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    rid = get_or_create_request_id(request)
    body = await _load_clear_images_json(request)
    delete_tags = bool(body["delete_tags"])

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)
    catalog = resolve_catalog_store(getattr(request.app.state, "catalog_store", None))
    tags = resolve_tag_store(getattr(request.app.state, "tag_store", None))

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            # Tags via TagStore; clear links (and optional Tag rows) first.
            deleted_image_tags = await tags.clear_all_image_tags(session)
            deleted_images = await catalog.clear_all_images(session)
            deleted_tags = 0
            if delete_tags:
                deleted_tags = await tags.clear_all_tags(session)

            await session.commit()

        return admin_ok(
            request,
            payload={
                "deleted_image_tags": int(deleted_image_tags),
                "deleted_images": int(deleted_images),
                "deleted_tags": int(deleted_tags),
            },
            request_id=rid,
        )

    result = await with_sqlite_busy_retry(_op)
    # Full catalog wipe → empty engine snapshot (best-effort).
    await maybe_publish_engine_empty_snapshot(
        client=getattr(request.app.state, "httpx_client", None),
        revision="cleared",
    )
    return result
