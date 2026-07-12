from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings, load_settings
from app.core.random_delivery import resolve_catalog_store
from app.core.random_engine_client import (
    engine_apply_events,
    engine_apply_snapshot,
    random_engine_base_url,
)
from app.db.catalog import CatalogStore
from app.db.session import create_sessionmaker
from app.db.tag_store import TagStore, resolve_tag_store

logger = logging.getLogger(__name__)

# Keep event batches modest for worker/admin best-effort publish.
_ENGINE_EVENT_CHUNK = 200
_ENGINE_EVENTS_TIMEOUT_S = 8.0


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
) -> dict[str, Any]:
    """Build a full snapshot of enabled images + tag names for the Go engine."""
    store = resolve_catalog_store(catalog)
    tags = resolve_tag_store(tag_store)
    images = await store.list_enabled_images(session, limit=limit)
    image_ids = [int(im.id) for im in images]
    tag_names_by_image = await tags.map_tag_names_by_image_ids(session, image_ids=image_ids)

    payload_images: list[dict[str, Any]] = []
    for im in images:
        payload_images.append(
            image_row_to_engine_payload(im, tag_names=tag_names_by_image.get(int(im.id), []))
        )
    return {"images": payload_images, "count": len(payload_images)}


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
) -> dict[str, Any] | None:
    Session = create_sessionmaker(engine)
    async with Session() as session:
        built = await build_engine_snapshot_payload(
            session, limit=limit, catalog=catalog, tag_store=tag_store
        )
    result = await engine_apply_snapshot(
        client,
        base_url,
        revision=revision,
        images=list(built["images"]),
        timeout_s=timeout_s,
    )
    if result is None:
        logger.warning("random-engine snapshot push failed revision=%s count=%s", revision, built["count"])
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
) -> dict[str, Any] | None:
    if not events:
        return {"ok": True, "applied": 0}
    last: dict[str, Any] | None = None
    for offset in range(0, len(events), _ENGINE_EVENT_CHUNK):
        chunk = events[offset : offset + _ENGINE_EVENT_CHUNK]
        result = await engine_apply_events(client, base_url, events=chunk, timeout_s=timeout_s)
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
        result = await publish_engine_events(base_url=base, client=use_client, events=events)
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
        result = await publish_engine_events(base_url=base, client=use_client, events=events)
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
        result = await engine_apply_snapshot(
            use_client,
            base,
            revision=revision,
            images=[],
            timeout_s=30.0,
        )
        if result is None:
            logger.warning("random-engine empty snapshot after clear failed")
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
