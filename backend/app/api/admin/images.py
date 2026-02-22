from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request

from app.api.admin.deps import get_admin_claims
from app.core.errors import ApiError, ErrorCode
from app.core.request_id import get_or_create_request_id
from app.db.models.image_tags import ImageTag
from app.db.models.images import Image
from app.db.models.tags import Tag
from app.db.session import create_sessionmaker, with_sqlite_busy_retry

router = APIRouter()

_ALLOWED_MISSING = {"tags", "geometry", "r18", "ai", "illust_type", "user", "title", "created_at", "popularity"}


def _parse_missing(values: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        for part in str(raw or "").replace(",", "|").split("|"):
            key = part.strip().lower()
            if not key or key in seen:
                continue
            if key not in _ALLOWED_MISSING:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported missing", status_code=400)
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

    missing_keys = _parse_missing(missing)

    rid = get_or_create_request_id(request)

    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    tag_counts = (
        sa.select(ImageTag.image_id.label("image_id"), sa.func.count().label("tag_count"))
        .group_by(ImageTag.image_id)
        .subquery()
    )
    tag_count_col = sa.func.coalesce(tag_counts.c.tag_count, 0).label("tag_count")

    stmt = (
        sa.select(Image, tag_count_col)
        .outerjoin(tag_counts, tag_counts.c.image_id == Image.id)
        .where(Image.status == 1)
        .order_by(Image.id.desc())
        .limit(int(limit) + 1)
    )
    if cursor_i is not None:
        stmt = stmt.where(Image.id < int(cursor_i))

    for key in missing_keys:
        if key == "tags":
            stmt = stmt.where(tag_count_col == 0)
        elif key == "geometry":
            stmt = stmt.where((Image.width.is_(None)) | (Image.height.is_(None)))
        elif key == "r18":
            stmt = stmt.where(Image.x_restrict.is_(None))
        elif key == "ai":
            stmt = stmt.where(Image.ai_type.is_(None))
        elif key == "illust_type":
            stmt = stmt.where(Image.illust_type.is_(None))
        elif key == "user":
            stmt = stmt.where(Image.user_id.is_(None))
        elif key == "title":
            stmt = stmt.where((Image.title.is_(None)) | (sa.func.trim(Image.title) == ""))
        elif key == "created_at":
            stmt = stmt.where((Image.created_at_pixiv.is_(None)) | (sa.func.trim(Image.created_at_pixiv) == ""))
        elif key == "popularity":
            stmt = stmt.where(
                (Image.bookmark_count.is_(None)) | (Image.view_count.is_(None)) | (Image.comment_count.is_(None))
            )

    async with Session() as session:
        rows = (await session.execute(stmt)).all()

    rows_page = rows[: int(limit)]
    next_cursor = int(rows_page[-1][0].id) if len(rows) > int(limit) and rows_page else None

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

    return {
        "ok": True,
        "items": items,
        "next_cursor": str(next_cursor) if next_cursor is not None else "",
        "request_id": rid,
    }


async def _load_bulk_delete_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc
    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)

    raw_ids = data.get("image_ids", None)
    if raw_ids is None:
        raw_ids = data.get("ids", None)
    if raw_ids is None:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing image_ids", status_code=400)
    if not isinstance(raw_ids, list):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported image_ids", status_code=400)

    ids: list[int] = []
    seen: set[int] = set()
    for raw in raw_ids:
        try:
            i = int(raw)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported image_ids", status_code=400) from exc
        if i <= 0 or i in seen:
            continue
        seen.add(i)
        ids.append(i)

    if not ids:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Empty image_ids", status_code=400)
    if len(ids) > 20_000:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Too many image_ids", status_code=400)

    return {"image_ids": ids}


def _chunks(values: list[int], *, chunk_size: int) -> list[list[int]]:
    if chunk_size <= 0:
        return [values]
    out: list[list[int]] = []
    for i in range(0, len(values), chunk_size):
        out.append(values[i : i + chunk_size])
    return out


def _safe_rowcount(result: Any) -> int:
    try:
        rc = int(getattr(result, "rowcount", 0) or 0)
    except Exception:
        return 0
    return rc if rc > 0 else 0


@router.delete("/images/{image_id}")
async def delete_admin_image(
    image_id: int,
    request: Request,
    _claims: dict[str, Any] = Depends(get_admin_claims),
) -> dict[str, Any]:
    _ = _claims
    if int(image_id) <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid image id", status_code=400)

    rid = get_or_create_request_id(request)
    engine = request.app.state.engine
    Session = create_sessionmaker(engine)

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            row = await session.get(Image, int(image_id))
            if row is None:
                raise ApiError(code=ErrorCode.NOT_FOUND, message="Image not found", status_code=404)
            await session.execute(sa.delete(ImageTag).where(ImageTag.image_id == int(image_id)))
            await session.delete(row)
            await session.commit()

        return {"ok": True, "image_id": str(int(image_id)), "request_id": rid}

    return await with_sqlite_busy_retry(_op)


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

    async def _op() -> dict[str, Any]:
        deleted = 0
        found = 0
        async with Session() as session:
            for chunk in _chunks(ids, chunk_size=900):
                rows = (await session.execute(sa.select(Image.id).where(Image.id.in_(chunk)))).scalars().all()
                found += len(rows)

            for chunk in _chunks(ids, chunk_size=900):
                await session.execute(sa.delete(ImageTag).where(ImageTag.image_id.in_(chunk)))
                result = await session.execute(sa.delete(Image).where(Image.id.in_(chunk)))
                deleted += _safe_rowcount(result)

            await session.commit()

        missing = max(0, int(len(ids)) - int(found))
        return {
            "ok": True,
            "requested": int(len(ids)),
            "deleted": int(deleted),
            "missing": int(missing),
            "request_id": rid,
        }

    return await with_sqlite_busy_retry(_op)


async def _load_clear_images_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc
    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)

    confirm = data.get("confirm", False)
    if confirm not in {True, 1, "1", "true", "yes", "y", "on"}:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Missing confirm", status_code=400)

    delete_tags = data.get("delete_tags", True)
    delete_tags_bool = (
        bool(delete_tags)
        if isinstance(delete_tags, (bool, int))
        else str(delete_tags).strip().lower() in {"1", "true", "yes", "y", "on"}
    )

    return {"delete_tags": bool(delete_tags_bool)}


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

    async def _op() -> dict[str, Any]:
        async with Session() as session:
            result_links = await session.execute(sa.delete(ImageTag))
            result_images = await session.execute(sa.delete(Image))
            result_tags = None
            if delete_tags:
                result_tags = await session.execute(sa.delete(Tag))

            await session.commit()

        return {
            "ok": True,
            "deleted_image_tags": _safe_rowcount(result_links),
            "deleted_images": _safe_rowcount(result_images),
            "deleted_tags": _safe_rowcount(result_tags) if result_tags is not None else 0,
            "request_id": rid,
        }

    return await with_sqlite_busy_retry(_op)
