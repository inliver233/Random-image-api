from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import observe_random_engine_pick
from app.core.random_engine_client import engine_pick
from app.core.random_strategy import pick_by_quality, pick_by_random_key
from app.db.images_get import get_image_by_id, get_images_by_ids


def orientation_to_engine_str(orientation_code: int | None) -> str:
    if orientation_code == 1:
        return "portrait"
    if orientation_code == 2:
        return "landscape"
    if orientation_code == 3:
        return "square"
    return "any"


def build_engine_filters(
    *,
    r18: int,
    r18_strict: int,
    ai_type_raw: str,
    ai_type_i: int | None,
    illust_type_i: int | None,
    orientation_code: int | None,
    min_width_i: int,
    min_height_i: int,
    min_pixels_i: int,
    min_bookmarks_i: int,
    min_views_i: int,
    min_comments_i: int,
    included: list[str],
    excluded: list[str],
    exclude_image_ids: list[int] | set[int] | None = None,
    user_id: int | None = None,
    illust_id: int | None = None,
    created_from_norm: str | None = None,
    created_to_norm: str | None = None,
    fail_cooldown_before: str | None = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {
        "r18": int(r18),
        "r18_strict": int(r18_strict),
        "ai_type": ai_type_raw if ai_type_i is not None else "any",
        "illust_type": str(illust_type_i) if illust_type_i is not None else "any",
        "orientation": orientation_to_engine_str(orientation_code),
        "min_width": int(min_width_i),
        "min_height": int(min_height_i),
        "min_pixels": int(min_pixels_i),
        "min_bookmarks": int(min_bookmarks_i),
        "min_views": int(min_views_i),
        "min_comments": int(min_comments_i),
        "included_tags": list(included),
        "excluded_tags": list(excluded),
        "exclude_image_ids": [int(x) for x in (exclude_image_ids or [])],
    }
    if user_id is not None:
        filters["user_id"] = int(user_id)
    if illust_id is not None:
        filters["illust_id"] = int(illust_id)
    if created_from_norm is not None:
        filters["created_from"] = created_from_norm
    if created_to_norm is not None:
        filters["created_to"] = created_to_norm
    if fail_cooldown_before is not None:
        filters["fail_cooldown_before"] = fail_cooldown_before
    return filters


def build_engine_quality_params(
    *,
    strategy_norm: str,
    quality_samples_i: int,
    pick_mode_raw: str,
    temperature: float,
    score_weights: Mapping[str, Any],
    multipliers: Mapping[str, Any],
    freshness_half_life_days: float,
    velocity_smooth_days: float,
) -> dict[str, Any] | None:
    if strategy_norm != "quality":
        return None
    return {
        "samples": int(quality_samples_i),
        "pick_mode": pick_mode_raw,
        "temperature": float(temperature),
        "weights": dict(score_weights),
        "multipliers": dict(multipliers),
        "freshness_half_life_days": float(freshness_half_life_days),
        "velocity_smooth_days": float(velocity_smooth_days),
    }


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


def compose_engine_pick_payload(
    *,
    r18: int,
    r18_strict: int,
    ai_type_raw: str,
    ai_type_i: int | None,
    illust_type_i: int | None,
    orientation_code: int | None,
    min_width_i: int,
    min_height_i: int,
    min_pixels_i: int,
    min_bookmarks_i: int,
    min_views_i: int,
    min_comments_i: int,
    included: list[str],
    excluded: list[str],
    exclude_image_ids: list[int] | set[int] | None,
    user_id: int | None,
    illust_id: int | None,
    created_from_norm: str | None,
    created_to_norm: str | None,
    fail_cooldown_before: str | None,
    strategy_norm: str,
    quality_samples_i: int,
    pick_mode_raw: str,
    temperature: float,
    score_weights: Mapping[str, Any],
    multipliers: Mapping[str, Any],
    freshness_half_life_days: float,
    velocity_smooth_days: float,
    seed: str | None,
    limit: int = 1,
    debug: bool = False,
) -> dict[str, Any]:
    """Compose full /v1/pick body (filters + quality + seed) for single or batch picks."""
    engine_filters = build_engine_filters(
        r18=int(r18),
        r18_strict=int(r18_strict),
        ai_type_raw=ai_type_raw,
        ai_type_i=ai_type_i,
        illust_type_i=illust_type_i,
        orientation_code=orientation_code,
        min_width_i=int(min_width_i),
        min_height_i=int(min_height_i),
        min_pixels_i=int(min_pixels_i),
        min_bookmarks_i=int(min_bookmarks_i),
        min_views_i=int(min_views_i),
        min_comments_i=int(min_comments_i),
        included=included,
        excluded=excluded,
        exclude_image_ids=exclude_image_ids,
        user_id=user_id,
        illust_id=illust_id,
        created_from_norm=created_from_norm,
        created_to_norm=created_to_norm,
        fail_cooldown_before=fail_cooldown_before,
    )
    quality_params = build_engine_quality_params(
        strategy_norm=strategy_norm,
        quality_samples_i=int(quality_samples_i),
        pick_mode_raw=pick_mode_raw,
        temperature=float(temperature),
        score_weights=score_weights,
        multipliers=multipliers,
        freshness_half_life_days=float(freshness_half_life_days),
        velocity_smooth_days=float(velocity_smooth_days),
    )
    return build_engine_pick_payload(
        filters=engine_filters,
        strategy=strategy_norm,
        quality=quality_params,
        seed=seed or None,
        limit=int(limit),
        debug=bool(debug),
    )


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


async def try_pick_many_via_engine(
    *,
    client: Any,
    base_url: str,
    session: AsyncSession,
    payload: dict[str, Any],
    timeout_s: float = 0.8,
) -> tuple[list[Any], dict[str, Any]]:
    """Batch variant of try_pick_via_engine for /feed (limit>1)."""
    meta: dict[str, Any] = {"engine": True, "engine_url": base_url, "batch": True}
    data = await engine_pick(client, base_url, payload=payload, timeout_s=timeout_s)
    if data is None:
        meta["engine_status"] = "unavailable"
        return [], meta
    meta["engine_code"] = data.get("code")
    if data.get("debug") is not None:
        meta["engine_debug"] = data.get("debug")
    if not data.get("ok"):
        meta["engine_status"] = "not_ok"
        return [], meta
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        meta["engine_status"] = "no_match"
        return [], meta

    ids: list[int] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        try:
            ids.append(int(it.get("id")))
        except Exception:
            continue
    if not ids:
        meta["engine_status"] = "bad_item"
        return [], meta

    images = await get_images_by_ids(session, image_ids=ids)
    if not images:
        meta["engine_status"] = "db_miss"
        meta["engine_image_ids"] = ids
        return [], meta
    meta["engine_status"] = "ok"
    meta["engine_image_ids"] = [int(im.id) for im in images]
    meta["picked_by"] = "random_engine"
    meta["engine_count"] = len(images)
    return images, meta


async def pick_with_strategy(
    *,
    session: Any,
    settings: Any,
    httpx_client: Any,
    pick_ctx: Any,
    filters: Any,
    exclude_image_ids: list[int] | None = None,
) -> tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]:
    """Engine-first pick (feature flag) with Python random/quality fallback.

    ``pick_ctx`` is the resolved RandomPickContext plan; ``filters`` is ParsedRandomFilters.
    Routes stay thin adapters over this service entrypoint.
    """
    debug_base = dict(pick_ctx.debug_base)
    engine_enabled = bool(getattr(settings, "random_engine_enabled", False))
    engine_url = str(getattr(settings, "random_engine_url", "") or "").strip().rstrip("/")
    if engine_enabled and engine_url and httpx_client is not None:
        base_exclude = list(exclude_image_ids or [])
        exclude_set: set[int] = set(int(x) for x in base_exclude)
        if bool(pick_ctx.anti_repeat_enabled) and pick_ctx.recent_exclude_image_ids:
            exclude_set.update(int(x) for x in pick_ctx.recent_exclude_image_ids)

        payload = pick_ctx.build_engine_payload(
            filters=filters,
            exclude_image_ids=exclude_set,
            limit=1,
            debug=False,
        )
        timeout_s = float(getattr(settings, "random_engine_timeout_ms", 800) or 800) / 1000.0
        image, eng_meta = await try_pick_via_engine(
            client=httpx_client,
            base_url=engine_url,
            session=session,
            payload=payload,
            timeout_s=timeout_s,
        )
        if image is not None:
            try:
                observe_random_engine_pick(status="ok")
            except Exception:
                pass
            return image, {**debug_base, "attempts_used": 1, **eng_meta}
        # Keep engine miss meta so dual-run ops can see why Python took over.
        engine_status = str((eng_meta or {}).get("engine_status") or "fallback")
        try:
            observe_random_engine_pick(status=engine_status)
        except Exception:
            pass
        engine_fallback_meta = {
            "picked_by": "python",
            "engine_status": engine_status,
            **{k: v for k, v in (eng_meta or {}).items() if k not in {"picked_by"}},
        }
        debug_base = {**debug_base, **engine_fallback_meta}

    if pick_ctx.strategy_norm == "random":
        return await pick_by_random_key(
            session=session,
            rng=pick_ctx.rng,
            pick_kwargs=pick_ctx.pick_kwargs,
            exclude_image_ids=exclude_image_ids,
            anti_repeat_enabled=bool(pick_ctx.anti_repeat_enabled),
            recent_exclude_image_ids=pick_ctx.recent_exclude_image_ids,
            dedup_strict=bool(pick_ctx.dedup_strict),
            debug_base=debug_base,
        )

    return await pick_by_quality(
        session=session,
        rng=pick_ctx.rng,
        pick_kwargs=pick_ctx.pick_kwargs,
        exclude_image_ids=exclude_image_ids,
        anti_repeat_enabled=bool(pick_ctx.anti_repeat_enabled),
        recent_exclude_image_ids=pick_ctx.recent_exclude_image_ids,
        recent_image_ids=pick_ctx.recent_image_ids,
        recent_author_ids=pick_ctx.recent_author_ids,
        dedup_strict=bool(pick_ctx.dedup_strict),
        dedup_image_penalty=float(pick_ctx.dedup_image_penalty),
        dedup_author_penalty=float(pick_ctx.dedup_author_penalty),
        quality_samples_i=int(pick_ctx.quality_samples_i),
        pick_mode_raw=pick_ctx.pick_mode_raw,
        temperature=float(pick_ctx.temperature),
        score_weights=pick_ctx.score_weights,
        multipliers=pick_ctx.multipliers,
        freshness_half_life_days=float(pick_ctx.freshness_half_life_days),
        velocity_smooth_days=float(pick_ctx.velocity_smooth_days),
        time_boost_enabled=bool(pick_ctx.time_boost_enabled),
        debug_base=debug_base,
    )
