from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.random_engine_client import engine_pick
from app.db.images_get import get_image_by_id


def build_engine_pick_payload(
    *,
    filters: dict[str, Any],
    strategy: str,
    quality: dict[str, Any] | None,
    seed: str | None,
    limit: int = 1,
    debug: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "filters": filters,
        "strategy": strategy,
        "limit": int(limit),
        "debug": bool(debug),
    }
    if quality:
        body["quality"] = quality
    if seed:
        body["seed"] = seed
    return body


async def try_pick_via_engine(
    *,
    client: Any,
    base_url: str,
    session: AsyncSession,
    payload: dict[str, Any],
    timeout_s: float = 0.8,
) -> tuple[Any | None, dict[str, Any]]:
    """
    Call Go engine; on OK with items, load full Image row from SQLite by id.
    Returns (image_or_none, debug_meta). Never raises for transport failures.
    """
    meta: dict[str, Any] = {"engine": True, "engine_url": base_url}
    data = await engine_pick(client, base_url, payload=payload, timeout_s=timeout_s)
    if data is None:
        meta["engine_status"] = "unavailable"
        return None, meta
    meta["engine_code"] = data.get("code")
    if data.get("debug") is not None:
        meta["engine_debug"] = data.get("debug")
    if not data.get("ok"):
        meta["engine_status"] = "not_ok"
        return None, meta
    items = data.get("items")
    if not isinstance(items, list) or not items:
        meta["engine_status"] = "no_match"
        return None, meta
    first = items[0]
    if not isinstance(first, dict):
        meta["engine_status"] = "bad_item"
        return None, meta
    try:
        image_id = int(first.get("id"))
    except Exception:
        meta["engine_status"] = "bad_id"
        return None, meta
    image = await get_image_by_id(session, image_id=image_id)
    if image is None:
        meta["engine_status"] = "db_miss"
        meta["engine_image_id"] = image_id
        return None, meta
    meta["engine_status"] = "ok"
    meta["engine_image_id"] = image_id
    meta["picked_by"] = "random_engine"
    return image, meta
