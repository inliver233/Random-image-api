from __future__ import annotations

import time
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import observe_random_engine_pick
from app.core.random_delivery import resolve_catalog_store
from app.core.random_engine_client import engine_pick
from app.core.random_strategy import pick_by_quality, pick_by_random_key
from app.db.catalog import CatalogStore


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
    time_boost_enabled: bool = True,
    recent_image_ids: set[int] | list[int] | None = None,
    recent_author_ids: set[int] | list[int] | None = None,
    dedup_image_penalty: float = 0.0,
    dedup_author_penalty: float = 0.0,
) -> dict[str, Any] | None:
    if strategy_norm != "quality":
        return None
    weights = dict(score_weights)
    # Python score_image_with_time_boosts zeros freshness/velocity when time boost is off
    # (seeded picks). Engine has no separate flag — zero those weights instead.
    if not time_boost_enabled:
        weights["freshness"] = 0.0
        weights["bookmark_velocity"] = 0.0
    out: dict[str, Any] = {
        "samples": int(quality_samples_i),
        "pick_mode": pick_mode_raw,
        "temperature": float(temperature),
        "weights": weights,
        "multipliers": dict(multipliers),
        "freshness_half_life_days": float(freshness_half_life_days),
        "velocity_smooth_days": float(velocity_smooth_days),
    }
    # Soft anti-repeat (Python pick_by_quality logit penalties). Hard exclude is filters.exclude_image_ids;
    # these ids/penalties still matter for authors and for recent images beyond the SQL exclude cap.
    img_ids: list[int] = []
    auth_ids: list[int] = []
    try:
        img_ids = sorted({int(x) for x in (recent_image_ids or []) if int(x) > 0})
    except Exception:
        img_ids = []
    try:
        auth_ids = sorted({int(x) for x in (recent_author_ids or []) if int(x) > 0})
    except Exception:
        auth_ids = []
    if img_ids:
        out["recent_image_ids"] = img_ids
    if auth_ids:
        out["recent_author_ids"] = auth_ids
    if img_ids or auth_ids:
        out["dedup_image_penalty"] = float(dedup_image_penalty)
        out["dedup_author_penalty"] = float(dedup_author_penalty)
    return out


def build_engine_pick_payload(
    *,
    filters: dict[str, Any],
    strategy: str,
    quality: dict[str, Any] | None,
    seed: str | None,
    limit: int = 1,
    debug: bool = False,
    client_dedup_key: str | None = None,
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
    key = (client_dedup_key or "").strip()
    if key:
        body["client_dedup_key"] = key
    return body


def merge_engine_exclude_ids(
    *,
    pick_ctx: Any,
    exclude_image_ids: list[int] | set[int] | None = None,
) -> set[int]:
    """Union caller excludes with anti-repeat recent ids (SQL cap list on the plan).

    Matches Python's first quality/random draw: recent images are hard-excluded via
    SQL NOT IN. Soft logit penalties (quality.recent_*) still apply for authors and
    for image ids beyond RECENT_EXCLUDE_SQL_CAP. Non-strict re-sample without excludes
    is a rare empty-draw fallback on Python only — dual-run keeps the primary path.
    """
    out: set[int] = set(int(x) for x in (exclude_image_ids or []))
    if bool(getattr(pick_ctx, "anti_repeat_enabled", False)):
        recent = getattr(pick_ctx, "recent_exclude_image_ids", None) or []
        out.update(int(x) for x in recent)
    return out


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
    client_dedup_key: str | None = None,
    time_boost_enabled: bool = True,
    recent_image_ids: set[int] | list[int] | None = None,
    recent_author_ids: set[int] | list[int] | None = None,
    dedup_image_penalty: float = 0.0,
    dedup_author_penalty: float = 0.0,
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
        time_boost_enabled=bool(time_boost_enabled),
        recent_image_ids=recent_image_ids,
        recent_author_ids=recent_author_ids,
        dedup_image_penalty=float(dedup_image_penalty),
        dedup_author_penalty=float(dedup_author_penalty),
    )
    return build_engine_pick_payload(
        filters=engine_filters,
        strategy=strategy_norm,
        quality=quality_params,
        seed=seed or None,
        limit=int(limit),
        debug=bool(debug),
        client_dedup_key=client_dedup_key,
    )


def classify_engine_pick_response(data: dict[str, Any] | None) -> str:
    """Map engine /v1/pick JSON to a dual-run status label (before catalog rehydrate).

    empty_index: INDEX_NOT_READY or empty_index debug reason (warm snapshot needed).
    no_match: ok response with zero items (filters excluded everything).
    not_ok / unavailable / bad_item handled by callers when rehydrating.
    """
    if data is None:
        return "unavailable"
    if not data.get("ok"):
        return "not_ok"
    code = str(data.get("code") or "").strip().upper()
    if code in {"INDEX_NOT_READY", "EMPTY_INDEX"}:
        return "empty_index"
    debug = data.get("debug")
    if isinstance(debug, dict):
        reason = str(debug.get("reason") or "").strip().lower()
        if reason in {"empty_index", "index_not_ready"}:
            return "empty_index"
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return "no_match"
    return "ok"


class EnginePickImage:
    """Lightweight delivery DTO from Go PickItem (avoids SQLite rehydrate on happy path).

    Attribute surface matches what public delivery / side-effects read via getattr.
    last_ok_at is set to a non-null marker so should_mark_image_ok is False for edge/JSON
    paths (no byte proof, no fail-state without a row load). Local stream delivery must
    still force mark_ok when from_engine_item is True (see deliver_random_image_stream).
    """

    __slots__ = (
        "id",
        "illust_id",
        "page_index",
        "ext",
        "original_url",
        "width",
        "height",
        "orientation",
        "x_restrict",
        "ai_type",
        "illust_type",
        "user_id",
        "user_name",
        "title",
        "created_at_pixiv",
        "bookmark_count",
        "view_count",
        "comment_count",
        "status",
        "last_ok_at",
        "last_error_code",
        "added_at",
        "random_key",
        "from_engine_item",
    )

    def __init__(self, **kwargs: Any) -> None:
        for key in self.__slots__:
            setattr(self, key, kwargs.get(key))


def _nullable_int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except Exception:
        return None


def image_from_engine_pick_item(item: Mapping[str, Any] | dict[str, Any] | None) -> EnginePickImage | None:
    """Build delivery DTO from engine PickItem when id/ext/original_url are present.

    Returns None when the item cannot satisfy public URL/stream delivery without catalog.
    """
    if not isinstance(item, Mapping):
        return None
    try:
        image_id = int(item.get("id"))  # type: ignore[arg-type]
    except Exception:
        return None
    if image_id <= 0:
        return None
    ext = str(item.get("ext") or "").strip().lstrip(".")
    if not ext:
        return None
    origin = item.get("original_url")
    if origin is None:
        return None
    original_url = str(origin).strip()
    if not original_url:
        return None
    try:
        illust_id = int(item.get("illust_id") or 0)
    except Exception:
        illust_id = 0
    if illust_id <= 0:
        return None
    try:
        page_index = int(item.get("page_index") or 0)
    except Exception:
        page_index = 0
    return EnginePickImage(
        id=image_id,
        illust_id=illust_id,
        page_index=page_index,
        ext=ext,
        original_url=original_url,
        width=_nullable_int(item.get("width")),
        height=_nullable_int(item.get("height")),
        orientation=None,
        x_restrict=_nullable_int(item.get("x_restrict")),
        ai_type=_nullable_int(item.get("ai_type")),
        illust_type=_nullable_int(item.get("illust_type")),
        user_id=_nullable_int(item.get("user_id")),
        user_name=(str(item["user_name"]) if item.get("user_name") is not None else None),
        title=(str(item["title"]) if item.get("title") is not None else None),
        created_at_pixiv=(
            str(item["created_at_pixiv"]) if item.get("created_at_pixiv") is not None else None
        ),
        bookmark_count=_nullable_int(item.get("bookmark_count")),
        view_count=_nullable_int(item.get("view_count")),
        comment_count=_nullable_int(item.get("comment_count")),
        status=1,
        # Non-null → should_mark_image_ok is False without a catalog round-trip.
        last_ok_at="engine",
        last_error_code=None,
        added_at=None,
        random_key=None,
        from_engine_item=True,
    )


async def resolve_engine_pick_images(
    *,
    session: AsyncSession,
    items: list[Any],
    catalog: CatalogStore | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Prefer PickItem DTOs; fall back to catalog rehydrate for incomplete items.

    Preserves engine item order. Returns (images, resolve_meta) where resolve_meta has
    ``rehydrate`` bool and id lists for ops/debug.
    """
    store = resolve_catalog_store(catalog)
    images: list[Any] = []
    need_ids: list[int] = []
    dto_by_id: dict[int, EnginePickImage] = {}
    ordered_ids: list[int] = []

    for raw in items:
        if not isinstance(raw, Mapping):
            continue
        dto = image_from_engine_pick_item(raw)
        if dto is not None:
            dto_by_id[int(dto.id)] = dto
            ordered_ids.append(int(dto.id))
            continue
        try:
            image_id = int(raw.get("id"))  # type: ignore[arg-type]
        except Exception:
            continue
        if image_id <= 0:
            continue
        ordered_ids.append(image_id)
        need_ids.append(image_id)

    rehydrated: dict[int, Any] = {}
    if need_ids:
        rows = await store.get_images_by_ids(session, image_ids=need_ids)
        rehydrated = {int(im.id): im for im in rows}

    for image_id in ordered_ids:
        if image_id in dto_by_id:
            images.append(dto_by_id[image_id])
        elif image_id in rehydrated:
            images.append(rehydrated[image_id])

    return images, {
        "engine_dto_count": len(dto_by_id),
        "engine_rehydrate_count": len(rehydrated),
        "engine_rehydrate": bool(need_ids),
    }


async def try_pick_via_engine(
    *,
    client: Any,
    base_url: str,
    session: AsyncSession,
    payload: dict[str, Any],
    timeout_s: float = 0.8,
    catalog: CatalogStore | None = None,
    settings: Any | None = None,
) -> tuple[Any | None, dict[str, Any]]:
    """
    Call Go engine; prefer PickItem delivery DTO, catalog rehydrate only if incomplete.
    Returns (image_or_none, debug_meta). Never raises for transport failures.
    """
    meta: dict[str, Any] = {"engine": True, "engine_url": base_url}
    t0 = time.perf_counter()
    data = await engine_pick(
        client, base_url, payload=payload, timeout_s=timeout_s, settings=settings
    )
    meta["engine_rtt_s"] = max(0.0, time.perf_counter() - t0)
    if data is None:
        meta["engine_status"] = "unavailable"
        return None, meta
    meta["engine_code"] = data.get("code")
    if data.get("debug") is not None:
        meta["engine_debug"] = data.get("debug")
    classified = classify_engine_pick_response(data)
    if classified != "ok":
        meta["engine_status"] = classified
        return None, meta
    items = data.get("items")
    first = items[0] if isinstance(items, list) and items else None
    if not isinstance(first, dict):
        meta["engine_status"] = "bad_item"
        return None, meta
    images, resolve_meta = await resolve_engine_pick_images(
        session=session,
        items=[first],
        catalog=catalog,
    )
    meta.update(resolve_meta)
    if not images:
        try:
            meta["engine_image_id"] = int(first.get("id"))
        except Exception:
            pass
        meta["engine_status"] = "db_miss"
        return None, meta
    image = images[0]
    meta["engine_status"] = "ok"
    meta["engine_image_id"] = int(image.id)
    meta["picked_by"] = "random_engine"
    return image, meta


async def try_pick_many_via_engine(
    *,
    client: Any,
    base_url: str,
    session: AsyncSession,
    payload: dict[str, Any],
    timeout_s: float = 0.8,
    catalog: CatalogStore | None = None,
    settings: Any | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Batch variant of try_pick_via_engine for /feed (limit>1)."""
    meta: dict[str, Any] = {"engine": True, "engine_url": base_url, "batch": True}
    t0 = time.perf_counter()
    data = await engine_pick(
        client, base_url, payload=payload, timeout_s=timeout_s, settings=settings
    )
    meta["engine_rtt_s"] = max(0.0, time.perf_counter() - t0)
    if data is None:
        meta["engine_status"] = "unavailable"
        return [], meta
    meta["engine_code"] = data.get("code")
    if data.get("debug") is not None:
        meta["engine_debug"] = data.get("debug")
    classified = classify_engine_pick_response(data)
    if classified != "ok":
        meta["engine_status"] = classified
        return [], meta
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        meta["engine_status"] = "no_match"
        return [], meta

    images, resolve_meta = await resolve_engine_pick_images(
        session=session,
        items=list(raw_items),
        catalog=catalog,
    )
    meta.update(resolve_meta)
    if not images:
        meta["engine_status"] = "db_miss"
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
    catalog: CatalogStore | None = None,
    pick: Any | None = None,
    skip_engine: bool = False,
) -> tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]:
    """Engine-first pick (feature flag) with Python random/quality fallback.

    ``pick_ctx`` is the resolved RandomPickContext plan; ``filters`` is ParsedRandomFilters.
    Routes stay thin adapters over this service entrypoint.
    ``pick`` is optional RandomPickPort for the Python SQL ring path.
    ``skip_engine`` forces the Python path (used by /feed top-up after one engine
    batch and by /random stream retries after the first dual-run attempt).
    """
    from app.core.random_engine_client import (
        engine_circuit_allow,
        engine_circuit_record,
        random_engine_base_url,
        should_route_pick_to_engine,
    )
    from app.db.random_pick_port import resolve_random_pick

    store = resolve_catalog_store(catalog)
    pick_port = resolve_random_pick(pick)
    debug_base = dict(pick_ctx.debug_base)
    engine_url = random_engine_base_url(settings) if settings is not None else None
    # Traffic roll uses process RNG only — never pick_ctx.rng (seed must stay deterministic).
    route_engine = (
        not skip_engine
        and engine_url
        and httpx_client is not None
        and should_route_pick_to_engine(settings)
    )
    if route_engine and not engine_circuit_allow():
        # Process circuit open (or concurrent half-open) — skip engine RTT.
        try:
            observe_random_engine_pick(status="skipped_circuit")
        except Exception:
            pass
        debug_base = {
            **debug_base,
            "engine_status": "skipped_circuit",
            "picked_by": "python",
        }
    elif route_engine:
        exclude_set = merge_engine_exclude_ids(pick_ctx=pick_ctx, exclude_image_ids=exclude_image_ids)

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
            catalog=store,
            settings=settings,
        )
        rtt = (eng_meta or {}).get("engine_rtt_s")
        if image is not None:
            try:
                engine_circuit_record("ok")
                observe_random_engine_pick(status="ok", duration_s=rtt if isinstance(rtt, (int, float)) else None)
            except Exception:
                pass
            return image, {**debug_base, "attempts_used": 1, **eng_meta}
        # Keep engine miss meta so dual-run ops can see why Python took over.
        engine_status = str((eng_meta or {}).get("engine_status") or "fallback")
        try:
            engine_circuit_record(engine_status)
            observe_random_engine_pick(
                status=engine_status,
                duration_s=rtt if isinstance(rtt, (int, float)) else None,
            )
        except Exception:
            pass
        engine_fallback_meta = {
            "picked_by": "python",
            "engine_status": engine_status,
            **{k: v for k, v in (eng_meta or {}).items() if k not in {"picked_by"}},
        }
        debug_base = {**debug_base, **engine_fallback_meta}
    elif (
        not skip_engine
        and bool(getattr(settings, "random_engine_enabled", False))
        and engine_url
    ):
        # Engine on but this request stayed on Python (traffic percent / no client).
        # Do not count sticky stream/feed top-up skips (skip_engine=True) as traffic skips.
        try:
            observe_random_engine_pick(status="skipped_traffic")
        except Exception:
            pass
        debug_base = {
            **debug_base,
            "engine_status": "skipped_traffic",
            "engine_traffic_percent": int(getattr(settings, "random_engine_traffic_percent", 100) or 0),
        }
    elif skip_engine:
        # Sticky Python path after first dual-run attempt (/feed top-up, stream retry).
        # Debug only — do not observe skipped_sticky per top-up item (N× spam under partial
        # TRAFFIC_PERCENT / partial batch). Batch path already recorded the dual-run decision
        # (ok / unavailable / skipped_traffic / circuit). Default-off stays silent.
        engine_configured = bool(getattr(settings, "random_engine_enabled", False)) and bool(engine_url)
        if engine_configured:
            debug_base = {
                **debug_base,
                "engine_status": "skipped_sticky",
            }

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
            pick=pick_port,
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
        pick=pick_port,
    )
