from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.random_defaults import (
    build_pick_kwargs,
    build_random_debug_base,
    resolve_attempts,
    resolve_dedup,
    resolve_fail_cooldown_ms,
    resolve_quality_samples,
    resolve_r18_strict,
    resolve_recommendation_config,
    resolve_strategy,
)
from app.core.random_engine_pick import pick_with_strategy
from app.core.random_request import ParsedRandomFilters
from app.core.recent_dedup import MemoryRecentDedup, RecentDedupPort
from app.db.catalog import CatalogStore
from app.db.random_pick_port import RandomPickPort

# Cap NOT IN size for SQLite plan quality; remaining recent ids still apply logit penalties.
RECENT_EXCLUDE_SQL_CAP = 512


@runtime_checkable
class RandomService(Protocol):
    """Per-request pick plan used by public /random and /feed adapters.

    Implementations plan once (runtime defaults + dedup + quality), then pick via
    Go engine dual-run and/or Python SQLite fallback without routes owning that logic.
    """

    async def pick(
        self,
        *,
        session: Any,
        settings: Any,
        httpx_client: Any,
        filters: ParsedRandomFilters,
        exclude_image_ids: list[int] | None = None,
        catalog: CatalogStore | None = None,
        pick: RandomPickPort | None = None,
        skip_engine: bool = False,
    ) -> tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]: ...

    async def try_engine_batch(
        self,
        *,
        session: Any,
        settings: Any,
        httpx_client: Any,
        filters: ParsedRandomFilters,
        limit: int,
        exclude_image_ids: list[int] | set[int] | None = None,
        catalog: CatalogStore | None = None,
    ) -> tuple[list[Any], dict[str, Any] | None]: ...


@runtime_checkable
class RandomServiceFactory(Protocol):
    """App-level factory that builds a per-request RandomService (pick plan)."""

    backend: str

    def build_context(
        self,
        *,
        filters: ParsedRandomFilters,
        random_defaults: dict[str, Any],
        attempts: int | None,
        r18_strict: int | None,
        strategy: str | None,
        quality_samples: int | None,
        query_params: Any = None,
        recent_dedup: RecentDedupPort | None = None,
    ) -> RandomService: ...


@dataclass(slots=True)
class RandomPickContext:
    """RandomService plan: runtime defaults + dedup + quality + engine/Python pick port.

    Routes (/random, /feed) are thin adapters: build context once, then call pick / try_engine_batch.
    """

    attempts: int
    attempts_source: str
    r18_strict: int
    r18_strict_source: str
    fail_cooldown_ms: int
    fail_cooldown_source: str
    fail_cooldown_before: Any
    strategy_norm: str
    strategy_source: str
    quality_samples_i: int
    quality_samples_base: int
    quality_samples_multiplier: int
    quality_samples_scaled: bool
    quality_samples_source: str
    pick_mode_raw: str
    temperature: float
    score_weights: dict[str, float]
    multipliers: dict[str, Any]
    freshness_half_life_days: float
    velocity_smooth_days: float
    recommendation_source: str
    rec_override_keys: list[str]
    time_boost_enabled: bool
    anti_repeat_enabled: bool
    dedup_enabled_setting: bool
    dedup_window_s: float
    dedup_max_images: int
    dedup_max_authors: int
    dedup_strict: bool
    dedup_image_penalty: float
    dedup_author_penalty: float
    recent_image_ids: set[int]
    recent_author_ids: set[int]
    recent_exclude_image_ids: list[int]
    pick_kwargs: dict[str, Any]
    debug_base: dict[str, Any]
    rng: Any
    seed_norm: str

    def build_engine_payload(
        self,
        *,
        filters: ParsedRandomFilters,
        exclude_image_ids: list[int] | set[int] | None = None,
        limit: int = 1,
        debug: bool = False,
        client_dedup_key: str | None = None,
    ) -> dict[str, Any]:
        """Build Go engine /v1/pick body from this plan + public filters."""
        from app.core.random_engine_pick import compose_engine_pick_payload

        # When BFF anti-repeat is on, also pin an engine-side short window (OpenAPI client_dedup_key).
        # Complements filters.exclude_image_ids; fail-open if engine ignores the field.
        dedup_key = (client_dedup_key or "").strip() or None
        if dedup_key is None and bool(self.anti_repeat_enabled):
            dedup_key = "bff-anti-repeat"

        # Soft penalties only when anti-repeat is on (matches Python pick_by_quality).
        soft_imgs = self.recent_image_ids if bool(self.anti_repeat_enabled) else None
        soft_auths = self.recent_author_ids if bool(self.anti_repeat_enabled) else None
        img_pen = float(self.dedup_image_penalty) if bool(self.anti_repeat_enabled) else 0.0
        auth_pen = float(self.dedup_author_penalty) if bool(self.anti_repeat_enabled) else 0.0

        return compose_engine_pick_payload(
            r18=int(filters.r18),
            r18_strict=int(self.r18_strict),
            ai_type_raw=filters.ai_type_raw,
            ai_type_i=filters.ai_type_i,
            illust_type_i=filters.illust_type_i,
            orientation_code=filters.orientation_map[filters.layout_norm],
            min_width_i=int(filters.min_width_i),
            min_height_i=int(filters.min_height_i),
            min_pixels_i=int(filters.min_pixels_i),
            min_bookmarks_i=int(filters.min_bookmarks_i),
            min_views_i=int(filters.min_views_i),
            min_comments_i=int(filters.min_comments_i),
            included=filters.included,
            excluded=filters.excluded,
            exclude_image_ids=exclude_image_ids,
            user_id=filters.user_id,
            illust_id=filters.illust_id,
            created_from_norm=filters.created_from_norm,
            created_to_norm=filters.created_to_norm,
            fail_cooldown_before=self.fail_cooldown_before,
            strategy_norm=self.strategy_norm,
            quality_samples_i=int(self.quality_samples_i),
            pick_mode_raw=self.pick_mode_raw,
            temperature=float(self.temperature),
            score_weights=self.score_weights,
            multipliers=self.multipliers,
            freshness_half_life_days=float(self.freshness_half_life_days),
            velocity_smooth_days=float(self.velocity_smooth_days),
            seed=self.seed_norm or None,
            limit=int(limit),
            debug=bool(debug),
            client_dedup_key=dedup_key,
            time_boost_enabled=bool(self.time_boost_enabled),
            recent_image_ids=soft_imgs,
            recent_author_ids=soft_auths,
            dedup_image_penalty=img_pen,
            dedup_author_penalty=auth_pen,
        )

    async def try_engine_batch(
        self,
        *,
        session: Any,
        settings: Any,
        httpx_client: Any,
        filters: ParsedRandomFilters,
        limit: int,
        exclude_image_ids: list[int] | set[int] | None = None,
        catalog: CatalogStore | None = None,
    ) -> tuple[list[Any], dict[str, Any] | None]:
        """One-shot engine batch for /feed. Returns ([], None) when dual-run is off."""
        from app.core.metrics import observe_random_engine_pick
        from app.core.random_engine_client import (
            engine_circuit_allow,
            engine_circuit_record,
            random_engine_base_url,
            should_route_pick_to_engine,
        )
        from app.core.random_engine_pick import merge_engine_exclude_ids, try_pick_many_via_engine

        if settings is None or httpx_client is None:
            return [], None
        engine_url = random_engine_base_url(settings)
        # Traffic roll is independent of pick seed (self.rng).
        if not engine_url or not should_route_pick_to_engine(settings):
            return [], None
        if not engine_circuit_allow():
            try:
                observe_random_engine_pick(status="skipped_circuit")
            except Exception:
                pass
            return [], {
                "engine": True,
                "engine_url": engine_url,
                "engine_status": "skipped_circuit",
                "picked_by": "python",
                "batch": True,
            }

        exclude_set = merge_engine_exclude_ids(pick_ctx=self, exclude_image_ids=exclude_image_ids)

        payload = self.build_engine_payload(
            filters=filters,
            exclude_image_ids=exclude_set,
            limit=int(limit),
            debug=False,
        )
        timeout_s = float(getattr(settings, "random_engine_timeout_ms", 800) or 800) / 1000.0
        images, eng_meta = await try_pick_many_via_engine(
            client=httpx_client,
            base_url=engine_url,
            session=session,
            payload=payload,
            timeout_s=timeout_s,
            catalog=catalog,
            settings=settings,
        )
        engine_status = str((eng_meta or {}).get("engine_status") or "fallback")
        rtt = (eng_meta or {}).get("engine_rtt_s")
        try:
            engine_circuit_record(engine_status)
            observe_random_engine_pick(
                status=engine_status,
                duration_s=rtt if isinstance(rtt, (int, float)) else None,
            )
        except Exception:
            pass
        return list(images or []), eng_meta

    async def pick(
        self,
        *,
        session: Any,
        settings: Any,
        httpx_client: Any,
        filters: ParsedRandomFilters,
        exclude_image_ids: list[int] | None = None,
        catalog: CatalogStore | None = None,
        pick: RandomPickPort | None = None,
        skip_engine: bool = False,
    ) -> tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]:
        return await pick_with_strategy(
            session=session,
            settings=settings,
            httpx_client=httpx_client,
            pick_ctx=self,
            filters=filters,
            exclude_image_ids=exclude_image_ids,
            catalog=catalog,
            pick=pick,
            skip_engine=bool(skip_engine),
        )


def build_random_pick_context(
    *,
    filters: ParsedRandomFilters,
    random_defaults: dict[str, Any],
    attempts: int | None,
    r18_strict: int | None,
    strategy: str | None,
    quality_samples: int | None,
    query_params: Any = None,
    recent_dedup: RecentDedupPort | None = None,
) -> RandomPickContext:
    """Resolve runtime defaults + dedup window into a pick plan."""
    attempts_resolved = resolve_attempts(attempts, random_defaults)
    r18_strict_resolved = resolve_r18_strict(r18_strict, random_defaults)
    fail_cooldown_ms_i, fail_cooldown_source, fail_cooldown_before = resolve_fail_cooldown_ms(random_defaults)

    seed_norm = filters.seed_norm
    rng = random.Random(seed_norm) if seed_norm else random
    time_boost_enabled = not bool(seed_norm)

    dedup = resolve_dedup(random_defaults)
    dedup_enabled_setting = bool(dedup.enabled)
    dedup_window_s = float(dedup.window_s)
    dedup_max_images = int(dedup.max_images)
    dedup_max_authors = int(dedup.max_authors)
    dedup_strict = bool(dedup.strict)
    dedup_image_penalty = float(dedup.image_penalty)
    dedup_author_penalty = float(dedup.author_penalty)

    anti_repeat_enabled = (
        bool(dedup_enabled_setting)
        and bool(time_boost_enabled)
        and filters.user_id is None
        and filters.illust_id is None
    )
    recent_image_ids: set[int] = set()
    recent_author_ids: set[int] = set()
    recent_exclude_image_ids: list[int] = []
    if anti_repeat_enabled:
        store = recent_dedup if recent_dedup is not None else MemoryRecentDedup()
        recent_image_list, recent_author_list = store.get_lists(
            time.monotonic(),
            window_s=float(dedup_window_s),
            max_images=int(dedup_max_images),
            max_authors=int(dedup_max_authors),
        )
        recent_image_ids = set(int(x) for x in recent_image_list)
        recent_author_ids = set(int(x) for x in recent_author_list)
        if recent_image_list:
            recent_exclude_image_ids = list(
                dict.fromkeys(int(x) for x in recent_image_list[-RECENT_EXCLUDE_SQL_CAP:])
            )

    strategy_norm, strategy_source = resolve_strategy(strategy, random_defaults)
    quality_plan = resolve_quality_samples(
        quality_samples=quality_samples,
        random_defaults=random_defaults,
        strategy_norm=strategy_norm,
        time_boost_enabled=bool(time_boost_enabled),
        included=filters.included,
        excluded=filters.excluded,
        min_bookmarks_i=int(filters.min_bookmarks_i),
        min_views_i=int(filters.min_views_i),
        min_comments_i=int(filters.min_comments_i),
        min_pixels_i=int(filters.min_pixels_i),
        min_width_i=int(filters.min_width_i),
        min_height_i=int(filters.min_height_i),
        ai_type_i=filters.ai_type_i,
        illust_type_i=filters.illust_type_i,
        orientation_set=filters.orientation_map[filters.layout_norm] is not None,
        created_from_norm=filters.created_from_norm,
        created_to_norm=filters.created_to_norm,
        r18=int(filters.r18),
        anti_repeat_enabled=bool(anti_repeat_enabled),
    )
    rec_cfg = resolve_recommendation_config(
        random_defaults=random_defaults,
        query_params=query_params,
    )

    pick_kwargs = build_pick_kwargs(
        r18=int(filters.r18),
        r18_strict=int(r18_strict_resolved.value),
        ai_type_i=filters.ai_type_i,
        illust_type_i=filters.illust_type_i,
        orientation=filters.orientation_map[filters.layout_norm],
        min_width_i=int(filters.min_width_i),
        min_height_i=int(filters.min_height_i),
        min_pixels_i=int(filters.min_pixels_i),
        min_bookmarks_i=int(filters.min_bookmarks_i),
        min_views_i=int(filters.min_views_i),
        min_comments_i=int(filters.min_comments_i),
        included=filters.included,
        excluded=filters.excluded,
        user_id=filters.user_id,
        illust_id=filters.illust_id,
        created_from_norm=filters.created_from_norm,
        created_to_norm=filters.created_to_norm,
        fail_cooldown_before=fail_cooldown_before,
    )
    debug_base = build_random_debug_base(
        attempts=int(attempts_resolved.value),
        attempts_source=attempts_resolved.source,
        r18_strict=int(r18_strict_resolved.value),
        r18_strict_source=r18_strict_resolved.source,
        fail_cooldown_ms=int(fail_cooldown_ms_i),
        fail_cooldown_source=fail_cooldown_source,
        strategy_norm=strategy_norm,
        strategy_source=strategy_source,
        quality_samples_i=int(quality_plan.samples),
        quality_samples_base=int(quality_plan.base),
        quality_samples_multiplier=int(quality_plan.multiplier),
        quality_samples_scaled=bool(quality_plan.scaled),
        quality_samples_source=quality_plan.source,
        anti_repeat_enabled=bool(anti_repeat_enabled),
        dedup_enabled_setting=bool(dedup_enabled_setting),
        dedup_window_s=float(dedup_window_s),
        dedup_max_images=int(dedup_max_images),
        dedup_max_authors=int(dedup_max_authors),
        dedup_strict=bool(dedup_strict),
        dedup_image_penalty=float(dedup_image_penalty),
        dedup_author_penalty=float(dedup_author_penalty),
        time_boost_enabled=bool(time_boost_enabled),
        recommendation_source=rec_cfg.source,
        rec_override_keys=list(rec_cfg.query_override_keys or []),
        freshness_half_life_days=float(rec_cfg.freshness_half_life_days),
        velocity_smooth_days=float(rec_cfg.velocity_smooth_days),
    )

    return RandomPickContext(
        attempts=int(attempts_resolved.value),
        attempts_source=attempts_resolved.source,
        r18_strict=int(r18_strict_resolved.value),
        r18_strict_source=r18_strict_resolved.source,
        fail_cooldown_ms=int(fail_cooldown_ms_i),
        fail_cooldown_source=fail_cooldown_source,
        fail_cooldown_before=fail_cooldown_before,
        strategy_norm=strategy_norm,
        strategy_source=strategy_source,
        quality_samples_i=int(quality_plan.samples),
        quality_samples_base=int(quality_plan.base),
        quality_samples_multiplier=int(quality_plan.multiplier),
        quality_samples_scaled=bool(quality_plan.scaled),
        quality_samples_source=quality_plan.source,
        pick_mode_raw=rec_cfg.pick_mode,
        temperature=float(rec_cfg.temperature),
        score_weights=rec_cfg.score_weights,
        multipliers=rec_cfg.multipliers,
        freshness_half_life_days=float(rec_cfg.freshness_half_life_days),
        velocity_smooth_days=float(rec_cfg.velocity_smooth_days),
        recommendation_source=rec_cfg.source,
        rec_override_keys=list(rec_cfg.query_override_keys or []),
        time_boost_enabled=bool(time_boost_enabled),
        anti_repeat_enabled=bool(anti_repeat_enabled),
        dedup_enabled_setting=bool(dedup_enabled_setting),
        dedup_window_s=float(dedup_window_s),
        dedup_max_images=int(dedup_max_images),
        dedup_max_authors=int(dedup_max_authors),
        dedup_strict=bool(dedup_strict),
        dedup_image_penalty=float(dedup_image_penalty),
        dedup_author_penalty=float(dedup_author_penalty),
        recent_image_ids=recent_image_ids,
        recent_author_ids=recent_author_ids,
        recent_exclude_image_ids=recent_exclude_image_ids,
        pick_kwargs=pick_kwargs,
        debug_base=debug_base,
        rng=rng,
        seed_norm=seed_norm,
    )


class DefaultRandomServiceFactory:
    """Default RandomService factory: builds RandomPickContext per request."""

    backend: str = "default"

    def build_context(
        self,
        *,
        filters: ParsedRandomFilters,
        random_defaults: dict[str, Any],
        attempts: int | None,
        r18_strict: int | None,
        strategy: str | None,
        quality_samples: int | None,
        query_params: Any = None,
        recent_dedup: RecentDedupPort | None = None,
    ) -> RandomPickContext:
        return build_random_pick_context(
            filters=filters,
            random_defaults=random_defaults,
            attempts=attempts,
            r18_strict=r18_strict,
            strategy=strategy,
            quality_samples=quality_samples,
            query_params=query_params,
            recent_dedup=recent_dedup,
        )


def build_random_service_factory() -> RandomServiceFactory:
    """App wiring helper for app.state.random_service."""
    return DefaultRandomServiceFactory()
