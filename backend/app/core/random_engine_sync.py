from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import struct
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings, load_settings
from app.core.random_delivery import resolve_catalog_store
from app.core.random_engine_client import (
    engine_apply_events,
    engine_apply_snapshot,
    engine_health,
    random_engine_base_url,
)
from app.db.catalog import CatalogStore
from app.db.session import create_sessionmaker
from app.db.tag_store import TagStore, resolve_tag_store

logger = logging.getLogger(__name__)

# Keep event batches modest for worker/admin best-effort publish.
_ENGINE_EVENT_CHUNK = 200
_ENGINE_EVENTS_TIMEOUT_S = 8.0
# ENGINE-1: keyset page size when building full snapshot (tags mapped per page; avoids
# loading entire enabled catalog + giant IN lists in one go on 62万-row galleries).
_ENGINE_SNAPSHOT_PAGE = 2000
_ENGINE_IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "gif", "webp", "zip"})


def _snapshot_b64(value: Any) -> str | None:
    if value is None:
        return None
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


def _snapshot_optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def engine_snapshot_content_hash(
    images: list[dict[str, Any]],
    *,
    tag_postings: dict[str, list[int]] | None = None,
) -> str:
    """Hash the complete normalized Engine payload with a cross-language encoding."""
    rows: list[list[Any]] = []
    seen: set[int] = set()
    for image in images:
        try:
            image_id = int(image.get("id"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("snapshot contains invalid image id") from exc
        if image_id <= 0:
            raise ValueError("snapshot contains invalid image id")
        if image_id in seen:
            raise ValueError("snapshot contains duplicate image id")
        seen.add(image_id)
        illust_id = int(image.get("illust_id"))
        page_index = int(image.get("page_index"))
        ext = str(image.get("ext") or "")
        status = int(image.get("status"))
        if illust_id <= 0:
            raise ValueError("snapshot contains invalid illust_id")
        if page_index < 0:
            raise ValueError("snapshot contains invalid page_index")
        if ext not in _ENGINE_IMAGE_EXTS:
            raise ValueError("snapshot contains invalid ext")
        if status != 1:
            raise ValueError("snapshot contains disabled image")
        random_key = float(image.get("random_key"))
        if not math.isfinite(random_key) or random_key < 0 or random_key >= 1:
            raise ValueError("snapshot contains invalid random_key")
        tag_ids = sorted(int(tag_id) for tag_id in (image.get("tag_ids") or []))
        if any(tag_id <= 0 for tag_id in tag_ids) or len(tag_ids) != len(set(tag_ids)):
            raise ValueError("snapshot contains invalid tag_ids")
        tag_names = sorted(str(name) for name in (image.get("tag_names") or []))
        if any(not name or name.strip() != name for name in tag_names) or len(tag_names) != len(set(tag_names)):
            raise ValueError("snapshot contains invalid tag_names")
        rows.append(
            [
                image_id,
                illust_id,
                page_index,
                _snapshot_b64(ext),
                status,
                struct.pack(">d", random_key).hex(),
                _snapshot_optional_int(image.get("width")),
                _snapshot_optional_int(image.get("height")),
                _snapshot_optional_int(image.get("orientation")),
                _snapshot_optional_int(image.get("x_restrict")),
                _snapshot_optional_int(image.get("ai_type")),
                _snapshot_optional_int(image.get("illust_type")),
                _snapshot_optional_int(image.get("user_id")),
                _snapshot_b64(image.get("user_name")),
                _snapshot_b64(image.get("title")),
                _snapshot_b64(image.get("created_at_pixiv")),
                _snapshot_b64(image.get("added_at")),
                _snapshot_optional_int(image.get("bookmark_count")),
                _snapshot_optional_int(image.get("view_count")),
                _snapshot_optional_int(image.get("comment_count")),
                _snapshot_b64(image.get("original_url")),
                [_snapshot_b64(name) for name in tag_names],
                tag_ids,
                _snapshot_b64(image.get("last_fail_at")),
                _snapshot_b64(image.get("last_error_code")),
            ]
        )
    rows.sort(key=lambda row: int(row[0]))

    posting_rows: list[list[Any]] = []
    for raw_name, raw_ids in (tag_postings or {}).items():
        name = str(raw_name)
        ids = sorted(int(image_id) for image_id in raw_ids)
        if not name or name.strip() != name or len(ids) != len(set(ids)):
            raise ValueError("snapshot contains invalid tag_postings")
        if any(image_id not in seen for image_id in ids):
            raise ValueError("snapshot tag_postings reference unknown image")
        posting_rows.append([_snapshot_b64(name), ids])
    posting_rows.sort(key=lambda row: str(row[0]))

    encoded = json.dumps([rows, posting_rows], ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_ack_matches(
    result: dict[str, Any] | None,
    *,
    revision: str,
    expected_count: int,
    content_hash: str,
    base_state_version: int,
) -> bool:
    if not isinstance(result, dict):
        return False
    expected_ready = int(expected_count) > 0
    try:
        return bool(
            result.get("ok") is True
            and str(result.get("revision") or "") == str(revision)
            and int(result.get("index_size")) == int(expected_count)
            and str(result.get("content_hash") or "") == str(content_hash)
            and result.get("ready") is expected_ready
            and int(result.get("state_version")) == int(base_state_version) + 1
        )
    except (TypeError, ValueError):
        return False


async def _begin_consistent_snapshot_read(session: AsyncSession) -> None:
    """Begin one explicit read snapshot for count plus every keyset page."""
    bind = session.get_bind()
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
    if dialect == "postgresql":
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        return
    if dialect == "sqlite":
        # Python 3.11 sqlite legacy transaction control does not BEGIN for SELECT.
        await session.execute(text("BEGIN"))
        return
    await session.begin()


def image_row_to_engine_payload(im: Any, *, tag_names: list[str] | None = None) -> dict[str, Any]:
    """Serialize one catalog image row (ORM or duck-typed) into Go engine IndexImage JSON."""
    names = [str(n).strip() for n in (tag_names or []) if str(n or "").strip()]
    return {
        "id": int(im.id),
        "illust_id": int(im.illust_id),
        "page_index": int(im.page_index),
        "ext": str(im.ext),
        "status": int(im.status),
        "random_key": float(im.random_key),
        "width": im.width,
        "height": im.height,
        "orientation": im.orientation,
        "x_restrict": im.x_restrict,
        "ai_type": im.ai_type,
        "illust_type": im.illust_type,
        "user_id": im.user_id,
        "user_name": im.user_name,
        "title": im.title,
        "created_at_pixiv": im.created_at_pixiv,
        "added_at": getattr(im, "added_at", None),
        "bookmark_count": im.bookmark_count,
        "view_count": im.view_count,
        "comment_count": im.comment_count,
        "original_url": im.original_url,
        "tag_names": names,
        "last_fail_at": im.last_fail_at,
        "last_error_code": im.last_error_code,
    }


async def load_engine_images_by_ids(
    session: AsyncSession,
    *,
    image_ids: list[int],
    catalog: CatalogStore | None = None,
    tag_store: TagStore | None = None,
) -> list[dict[str, Any]]:
    """Load images (any status) by id and serialize for engine events/snapshot."""
    store = resolve_catalog_store(catalog)
    tags = resolve_tag_store(tag_store)
    rows = await store.get_images_by_ids_any_status(session, image_ids=list(image_ids))
    if not rows:
        return []
    ids = [int(im.id) for im in rows]
    tag_names = await tags.map_tag_names_by_image_ids(session, image_ids=ids)
    return [image_row_to_engine_payload(im, tag_names=tag_names.get(int(im.id), [])) for im in rows]


async def load_engine_images_by_illust(
    session: AsyncSession,
    *,
    illust_id: int,
    catalog: CatalogStore | None = None,
    tag_store: TagStore | None = None,
) -> list[dict[str, Any]]:
    store = resolve_catalog_store(catalog)
    tags = resolve_tag_store(tag_store)
    rows = await store.get_images_by_illust_id(session, illust_id=int(illust_id))
    if not rows:
        return []
    ids = [int(im.id) for im in rows]
    tag_names = await tags.map_tag_names_by_image_ids(session, image_ids=ids)
    return [image_row_to_engine_payload(im, tag_names=tag_names.get(int(im.id), [])) for im in rows]


def build_upsert_events(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "image_upserted", "image": im} for im in images if isinstance(im, dict) and im.get("id")]


def build_delete_events(image_ids: list[int]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in image_ids:
        try:
            i = int(raw)
        except Exception:
            continue
        if i <= 0 or i in seen:
            continue
        seen.add(i)
        events.append({"type": "image_deleted", "image_id": i})
    return events


async def build_engine_snapshot_payload(
    session: AsyncSession,
    *,
    limit: int | None = None,
    catalog: CatalogStore | None = None,
    tag_store: TagStore | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    """Build a full snapshot of enabled images + tag names for the Go engine.

    ENGINE-1: walks enabled images with keyset pagination (``after_id``) and maps
    tags per page (IN lists already chunked at 900). Optional ``limit`` still caps
    total rows for tests/partial warm.
    """
    store = resolve_catalog_store(catalog)
    tags = resolve_tag_store(tag_store)
    authoritative_count = await store.count_enabled_images(session)
    page = int(page_size) if page_size is not None and int(page_size) > 0 else _ENGINE_SNAPSHOT_PAGE
    if page < 1:
        page = _ENGINE_SNAPSHOT_PAGE
    total_cap = int(limit) if limit is not None and int(limit) > 0 else None

    payload_images: list[dict[str, Any]] = []
    after_id: int | None = None
    while True:
        remaining = None if total_cap is None else max(0, total_cap - len(payload_images))
        if remaining is not None and remaining == 0:
            break
        fetch_n = page if remaining is None else min(page, remaining)
        batch = await store.list_enabled_images(session, limit=fetch_n, after_id=after_id)
        if not batch:
            break
        image_ids = [int(im.id) for im in batch]
        tag_names_by_image = await tags.map_tag_names_by_image_ids(session, image_ids=image_ids)
        for im in batch:
            payload_images.append(
                image_row_to_engine_payload(im, tag_names=tag_names_by_image.get(int(im.id), []))
            )
        after_id = int(batch[-1].id)
        if len(batch) < fetch_n:
            break
        if total_cap is not None and len(payload_images) >= total_cap:
            break
    content_hash = engine_snapshot_content_hash(payload_images)
    return {
        "images": payload_images,
        "count": len(payload_images),
        "authoritative_count": int(authoritative_count),
        "content_hash": content_hash,
    }


async def push_engine_snapshot(
    engine: AsyncEngine,
    *,
    base_url: str,
    client: Any,
    revision: str,
    limit: int | None = None,
    timeout_s: float = 60.0,
    catalog: CatalogStore | None = None,
    tag_store: TagStore | None = None,
    settings: Settings | Any | None = None,
) -> dict[str, Any] | None:
    if limit is not None:
        raise ValueError("partial snapshot limits cannot be applied to the random engine")
    revision = str(revision or "").strip()
    if not revision:
        raise ValueError("snapshot revision is required")
    health = await engine_health(client, base_url, timeout_s=min(float(timeout_s), 2.0))
    try:
        base_state_version = int((health or {}).get("state_version"))
    except (TypeError, ValueError):
        logger.warning("random-engine snapshot preflight missing state_version")
        return None
    if base_state_version < 0:
        logger.warning("random-engine snapshot preflight invalid state_version=%s", base_state_version)
        return None
    Session = create_sessionmaker(engine)
    async with Session() as session:
        await _begin_consistent_snapshot_read(session)
        built = await build_engine_snapshot_payload(
            session, limit=limit, catalog=catalog, tag_store=tag_store
        )
    if int(built["count"]) != int(built["authoritative_count"]):
        logger.warning(
            "random-engine snapshot incomplete built=%s authoritative=%s",
            built["count"],
            built["authoritative_count"],
        )
        return None
    result = await engine_apply_snapshot(
        client,
        base_url,
        revision=revision,
        images=list(built["images"]),
        complete=True,
        expected_count=int(built["count"]),
        content_hash=str(built["content_hash"]),
        base_state_version=base_state_version,
        timeout_s=timeout_s,
        settings=settings,
    )
    if result is None:
        logger.warning("random-engine snapshot push failed revision=%s count=%s", revision, built["count"])
        return None
    if not _snapshot_ack_matches(
        result,
        revision=revision,
        expected_count=int(built["count"]),
        content_hash=str(built["content_hash"]),
        base_state_version=base_state_version,
    ):
        logger.warning("random-engine snapshot acknowledgement mismatch revision=%s", revision)
        return None
    return result


async def maybe_warm_engine_snapshot_on_startup(
    db_engine: AsyncEngine,
    *,
    settings: Settings | None = None,
    client: Any | None = None,
    timeout_s: float = 120.0,
    limit: int | None = None,
    catalog: CatalogStore | None = None,
    tag_store: TagStore | None = None,
) -> dict[str, Any] | None:
    """Best-effort full snapshot when RANDOM_ENGINE_URL is set.

    No-op without URL. Never raises. Does **not** require RANDOM_ENGINE_ENABLED so the
    index can warm before dual-run cutover (same gate as catalog event publish).
    """
    owned: httpx.AsyncClient | None = None
    try:
        from datetime import datetime, timezone

        base, use_client, owned = await _resolve_publish_client(settings=settings, client=client)
        if not base or use_client is None:
            return None
        revision = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        result = await push_engine_snapshot(
            db_engine,
            base_url=base,
            client=use_client,
            revision=revision,
            limit=limit,
            timeout_s=float(timeout_s),
            catalog=catalog,
            tag_store=tag_store,
            settings=settings,
        )
        if result is not None:
            logger.info(
                "random-engine startup snapshot ok revision=%s",
                revision,
            )
        return result
    except Exception as exc:
        logger.warning("random-engine startup snapshot failed: %s", exc)
        return None
    finally:
        if owned is not None:
            try:
                await owned.aclose()
            except Exception:
                pass


async def _resolve_publish_client(
    *,
    settings: Settings | None,
    client: Any | None,
) -> tuple[str | None, Any | None, httpx.AsyncClient | None]:
    """Return (base_url, client_to_use, owned_client_to_close).

    When ``client`` is omitted, reuse the process control-plane singleton
    (worker hydrate/import/heal). Shared client must not be closed by callers
    (owned is always None in that path).
    """
    s = settings if settings is not None else load_settings()
    base = random_engine_base_url(s)
    if not base:
        return None, None, None
    if client is not None:
        return base, client, None
    from app.core.http_client import get_control_plane_http_client

    return base, get_control_plane_http_client(), None


async def publish_engine_events(
    *,
    base_url: str,
    client: Any,
    events: list[dict[str, Any]],
    timeout_s: float = _ENGINE_EVENTS_TIMEOUT_S,
    settings: Settings | Any | None = None,
) -> dict[str, Any] | None:
    if not events:
        return {"ok": True, "applied": 0}
    last: dict[str, Any] | None = None
    for offset in range(0, len(events), _ENGINE_EVENT_CHUNK):
        chunk = events[offset : offset + _ENGINE_EVENT_CHUNK]
        result = await engine_apply_events(
            client, base_url, events=chunk, timeout_s=timeout_s, settings=settings
        )
        if result is None:
            return None
        last = result
    return last


async def maybe_publish_engine_upserts(
    db_engine: AsyncEngine,
    *,
    image_ids: list[int] | None = None,
    illust_id: int | None = None,
    settings: Settings | None = None,
    client: Any | None = None,
    catalog: CatalogStore | None = None,
    tag_store: TagStore | None = None,
) -> dict[str, Any] | None:
    """
    Best-effort catalog upsert → Go engine /v1/admin/events.
    No-op when RANDOM_ENGINE_URL is unset. Never raises.
    Gated on URL only (not RANDOM_ENGINE_ENABLED) so the index can warm before dual-run pick.
    """
    owned: httpx.AsyncClient | None = None
    try:
        base, use_client, owned = await _resolve_publish_client(settings=settings, client=client)
        if not base or use_client is None:
            return None

        Session = create_sessionmaker(db_engine)
        async with Session() as session:
            if image_ids:
                images = await load_engine_images_by_ids(
                    session,
                    image_ids=list(image_ids),
                    catalog=catalog,
                    tag_store=tag_store,
                )
            elif illust_id is not None and int(illust_id) > 0:
                images = await load_engine_images_by_illust(
                    session,
                    illust_id=int(illust_id),
                    catalog=catalog,
                    tag_store=tag_store,
                )
            else:
                return None

        events = build_upsert_events(images)
        if not events:
            return None
        result = await publish_engine_events(
            base_url=base, client=use_client, events=events, settings=settings
        )
        if result is None:
            logger.warning(
                "random-engine upsert publish failed count=%s illust_id=%s",
                len(events),
                illust_id,
            )
        return result
    except Exception as exc:
        logger.warning("random-engine upsert publish error: %s", exc)
        return None
    finally:
        if owned is not None:
            try:
                await owned.aclose()
            except Exception:
                pass


async def maybe_publish_engine_deletes(
    *,
    image_ids: list[int],
    settings: Settings | None = None,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Best-effort image_deleted events. No-op without URL. Never raises."""
    owned: httpx.AsyncClient | None = None
    try:
        base, use_client, owned = await _resolve_publish_client(settings=settings, client=client)
        if not base or use_client is None:
            return None
        events = build_delete_events(image_ids)
        if not events:
            return None
        result = await publish_engine_events(
            base_url=base, client=use_client, events=events, settings=settings
        )
        if result is None:
            logger.warning("random-engine delete publish failed count=%s", len(events))
        return result
    except Exception as exc:
        logger.warning("random-engine delete publish error: %s", exc)
        return None
    finally:
        if owned is not None:
            try:
                await owned.aclose()
            except Exception:
                pass


async def maybe_publish_engine_empty_snapshot(
    *,
    settings: Settings | None = None,
    client: Any | None = None,
    revision: str = "cleared",
) -> dict[str, Any] | None:
    """After catalog clear: replace engine index with empty set. Best-effort."""
    owned: httpx.AsyncClient | None = None
    try:
        base, use_client, owned = await _resolve_publish_client(settings=settings, client=client)
        if not base or use_client is None:
            return None
        health = await engine_health(use_client, base, timeout_s=2.0)
        try:
            base_state_version = int((health or {}).get("state_version"))
        except (TypeError, ValueError):
            logger.warning("random-engine empty snapshot preflight missing state_version")
            return None
        if base_state_version < 0:
            logger.warning("random-engine empty snapshot preflight invalid state_version=%s", base_state_version)
            return None
        content_hash = engine_snapshot_content_hash([])
        result = await engine_apply_snapshot(
            use_client,
            base,
            revision=revision,
            images=[],
            complete=True,
            expected_count=0,
            content_hash=content_hash,
            base_state_version=base_state_version,
            timeout_s=30.0,
            settings=settings,
        )
        if result is None:
            logger.warning("random-engine empty snapshot after clear failed")
            return None
        if not _snapshot_ack_matches(
            result,
            revision=revision,
            expected_count=0,
            content_hash=content_hash,
            base_state_version=base_state_version,
        ):
            logger.warning("random-engine empty snapshot acknowledgement mismatch")
            return None
        return result
    except Exception as exc:
        logger.warning("random-engine empty snapshot error: %s", exc)
        return None
    finally:
        if owned is not None:
            try:
                await owned.aclose()
            except Exception:
                pass
