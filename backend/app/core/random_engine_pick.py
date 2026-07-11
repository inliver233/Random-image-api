from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.random_engine_client import engine_pick
from app.core.random_strategy import pick_by_quality, pick_by_random_key
from app.db.images_get import get_image_by_id


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


async def pick_with_strategy(
    *,
    session: Any,
    settings: Any,
    httpx_client: Any,
    rng: Any,
    pick_kwargs: dict[str, Any],
    debug_base: dict[str, Any],
    strategy_norm: str,
    seed_norm: str,
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
    user_id: int | None,
    illust_id: int | None,
    created_from_norm: str | None,
    created_to_norm: str | None,
    fail_cooldown_before: str | None,
    quality_samples_i: int,
    pick_mode_raw: str,
    temperature: float,
    score_weights: Mapping[str, Any],
    multipliers: Mapping[str, Any],
    freshness_half_life_days: float,
    velocity_smooth_days: float,
    time_boost_enabled: bool,
    anti_repeat_enabled: bool,
    recent_exclude_image_ids: list[int],
    recent_image_ids: set[int],
    recent_author_ids: set[int],
    dedup_strict: bool,
    dedup_image_penalty: float,
    dedup_author_penalty: float,
    exclude_image_ids: list[int] | None = None,
) -> tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]:
    """Engine-first pick (feature flag) with Python random/quality fallback."""
    engine_enabled = bool(getattr(settings, "random_engine_enabled", False))
    engine_url = str(getattr(settings, "random_engine_url", "") or "").strip().rstrip("/")
    if engine_enabled and engine_url and httpx_client is not None:
        base_exclude = list(exclude_image_ids or [])
        exclude_set: set[int] = set(int(x) for x in base_exclude)
        if bool(anti_repeat_enabled) and recent_exclude_image_ids:
            exclude_set.update(int(x) for x in recent_exclude_image_ids)

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
            exclude_image_ids=exclude_set,
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
        payload = build_engine_pick_payload(
            filters=engine_filters,
            strategy=strategy_norm,
            quality=quality_params,
            seed=seed_norm or None,
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
            return image, {**debug_base, "attempts_used": 1, **eng_meta}

    if strategy_norm == "random":
        return await pick_by_random_key(
            session=session,
            rng=rng,
            pick_kwargs=pick_kwargs,
            exclude_image_ids=exclude_image_ids,
            anti_repeat_enabled=bool(anti_repeat_enabled),
            recent_exclude_image_ids=recent_exclude_image_ids,
            dedup_strict=bool(dedup_strict),
            debug_base=debug_base,
        )

    return await pick_by_quality(
        session=session,
        rng=rng,
        pick_kwargs=pick_kwargs,
        exclude_image_ids=exclude_image_ids,
        anti_repeat_enabled=bool(anti_repeat_enabled),
        recent_exclude_image_ids=recent_exclude_image_ids,
        recent_image_ids=recent_image_ids,
        recent_author_ids=recent_author_ids,
        dedup_strict=bool(dedup_strict),
        dedup_image_penalty=float(dedup_image_penalty),
        dedup_author_penalty=float(dedup_author_penalty),
        quality_samples_i=int(quality_samples_i),
        pick_mode_raw=pick_mode_raw,
        temperature=float(temperature),
        score_weights=score_weights,
        multipliers=multipliers,
        freshness_half_life_days=float(freshness_half_life_days),
        velocity_smooth_days=float(velocity_smooth_days),
        time_boost_enabled=bool(time_boost_enabled),
        debug_base=debug_base,
    )
