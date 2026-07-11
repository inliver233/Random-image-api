from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any

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
from app.core.recent_dedup import get_recent_lists

# Cap NOT IN size for SQLite plan quality; remaining recent ids still apply logit penalties.
RECENT_EXCLUDE_SQL_CAP = 512


@dataclass(slots=True)
class RandomPickContext:
    """Resolved pick plan for /random (runtime defaults + dedup + quality + rec)."""

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

    async def pick(
        self,
        *,
        session: Any,
        settings: Any,
        httpx_client: Any,
        filters: ParsedRandomFilters,
        exclude_image_ids: list[int] | None = None,
    ) -> tuple[Any, dict[str, Any]] | tuple[None, dict[str, Any]]:
        return await pick_with_strategy(
            session=session,
            settings=settings,
            httpx_client=httpx_client,
            rng=self.rng,
            pick_kwargs=self.pick_kwargs,
            debug_base=self.debug_base,
            strategy_norm=self.strategy_norm,
            seed_norm=self.seed_norm,
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
            user_id=filters.user_id,
            illust_id=filters.illust_id,
            created_from_norm=filters.created_from_norm,
            created_to_norm=filters.created_to_norm,
            fail_cooldown_before=self.fail_cooldown_before,
            quality_samples_i=int(self.quality_samples_i),
            pick_mode_raw=self.pick_mode_raw,
            temperature=float(self.temperature),
            score_weights=self.score_weights,
            multipliers=self.multipliers,
            freshness_half_life_days=float(self.freshness_half_life_days),
            velocity_smooth_days=float(self.velocity_smooth_days),
            time_boost_enabled=bool(self.time_boost_enabled),
            anti_repeat_enabled=bool(self.anti_repeat_enabled),
            recent_exclude_image_ids=self.recent_exclude_image_ids,
            recent_image_ids=self.recent_image_ids,
            recent_author_ids=self.recent_author_ids,
            dedup_strict=bool(self.dedup_strict),
            dedup_image_penalty=float(self.dedup_image_penalty),
            dedup_author_penalty=float(self.dedup_author_penalty),
            exclude_image_ids=exclude_image_ids,
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
        recent_image_list, recent_author_list = get_recent_lists(
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
