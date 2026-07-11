from __future__ import annotations

import math
from typing import Any

from app.core.recommendation import multiplier_for_image, score_image_with_time_boosts
from app.db.random_pick_port import RandomPickPort, resolve_random_pick


async def pick_by_random_key(
    *,
    session: Any,
    rng: Any,
    pick_kwargs: dict[str, Any],
    exclude_image_ids: list[int] | None,
    anti_repeat_enabled: bool,
    recent_exclude_image_ids: list[int],
    dedup_strict: bool,
    debug_base: dict[str, Any],
    pick: RandomPickPort | None = None,
) -> tuple[Any | None, dict[str, Any]]:
    port = resolve_random_pick(pick)
    base_exclude = list(exclude_image_ids or [])
    exclude_set: set[int] = set(int(x) for x in base_exclude)
    if bool(anti_repeat_enabled) and recent_exclude_image_ids:
        exclude_set.update(int(x) for x in recent_exclude_image_ids)

    image = await port.pick_one(session, r=rng.random(), exclude_image_ids=list(exclude_set), **pick_kwargs)
    if image is None and bool(anti_repeat_enabled) and bool(recent_exclude_image_ids) and not bool(dedup_strict):
        image = await port.pick_one(session, r=rng.random(), exclude_image_ids=base_exclude, **pick_kwargs)
    if image is None:
        return None, {**debug_base, "attempts_used": 1, "picked_by": "random_key"}
    return image, {**debug_base, "attempts_used": 1, "picked_by": "random_key"}


async def pick_by_quality(
    *,
    session: Any,
    rng: Any,
    pick_kwargs: dict[str, Any],
    exclude_image_ids: list[int] | None,
    anti_repeat_enabled: bool,
    recent_exclude_image_ids: list[int],
    recent_image_ids: set[int],
    recent_author_ids: set[int],
    dedup_strict: bool,
    dedup_image_penalty: float,
    dedup_author_penalty: float,
    quality_samples_i: int,
    pick_mode_raw: str,
    temperature: float,
    score_weights: dict[str, float],
    multipliers: dict[str, float],
    freshness_half_life_days: float,
    velocity_smooth_days: float,
    time_boost_enabled: bool,
    debug_base: dict[str, Any],
    pick: RandomPickPort | None = None,
) -> tuple[Any | None, dict[str, Any]]:
    base_exclude_set: set[int] = set(int(x) for x in exclude_image_ids or [])
    exclude_set: set[int] = set(base_exclude_set)
    if bool(anti_repeat_enabled) and recent_exclude_image_ids:
        exclude_set.update(int(x) for x in recent_exclude_image_ids)
    # candidates: (image, score_total, multiplier, logit, score_base, freshness_contrib, velocity_contrib)
    candidates: list[tuple[Any, float, float, float, float, float, float]] = []

    # 批量抽样：一次性取 N 个候选（必要时 wrap-around 再取一次），避免 N 次 DB 循环查询。
    # 若用户把某些类别倍率设为 0（例如 manga=0），直接在 SQL 抽样阶段剔除，减少无效候选。
    ai_allowed: set[int | None] = set()
    if float(multipliers.get("ai", 1.0)) > 0.0:
        ai_allowed.add(1)
    if float(multipliers.get("non_ai", 1.0)) > 0.0:
        ai_allowed.add(0)
    if float(multipliers.get("unknown_ai", 1.0)) > 0.0:
        ai_allowed.add(None)

    illust_allowed: set[int | None] = set()
    if float(multipliers.get("illust", 1.0)) > 0.0:
        illust_allowed.add(0)
    if float(multipliers.get("manga", 1.0)) > 0.0:
        illust_allowed.add(1)
    if float(multipliers.get("ugoira", 1.0)) > 0.0:
        illust_allowed.add(2)
    if float(multipliers.get("unknown_illust_type", 1.0)) > 0.0:
        illust_allowed.add(None)

    if not ai_allowed or not illust_allowed:
        return None, {
            **debug_base,
            "attempts_used": 1,
            "picked_by": "quality_weighted" if pick_mode_raw == "weighted" else "quality_best",
            "candidates_drawn": 0,
            "candidates_accepted": 0,
            "quality_pick_mode": pick_mode_raw,
            "quality_temperature": float(temperature),
        }

    port = resolve_random_pick(pick)
    images = await port.pick_many(
        session,
        r=rng.random(),
        limit=int(quality_samples_i),
        exclude_image_ids=list(exclude_set),
        ai_type_allowed=ai_allowed,
        illust_type_allowed=illust_allowed,
        **pick_kwargs,
    )
    if not images and bool(anti_repeat_enabled) and bool(recent_exclude_image_ids) and not bool(dedup_strict):
        images = await port.pick_many(
            session,
            r=rng.random(),
            limit=int(quality_samples_i),
            exclude_image_ids=list(base_exclude_set),
            ai_type_allowed=ai_allowed,
            illust_type_allowed=illust_allowed,
            **pick_kwargs,
        )

    drawn = int(len(images))
    accepted = 0
    for image in images:
        score, score_base, freshness_contrib, velocity_contrib = score_image_with_time_boosts(
            image,
            score_weights=score_weights,
            freshness_half_life_days=float(freshness_half_life_days),
            velocity_smooth_days=float(velocity_smooth_days),
            time_boost_enabled=bool(time_boost_enabled),
        )
        multiplier = multiplier_for_image(image, multipliers=multipliers)
        if multiplier <= 0.0:
            continue

        logit = float(score) / float(temperature) + math.log(float(multiplier))
        if bool(anti_repeat_enabled):
            try:
                if int(getattr(image, "id", 0) or 0) in recent_image_ids:
                    logit -= float(dedup_image_penalty)
            except Exception:
                pass
            try:
                uid = getattr(image, "user_id", None)
                if uid is not None and int(uid) in recent_author_ids:
                    logit -= float(dedup_author_penalty)
            except Exception:
                pass
        candidates.append(
            (
                image,
                float(score),
                float(multiplier),
                float(logit),
                float(score_base),
                float(freshness_contrib),
                float(velocity_contrib),
            )
        )
        accepted += 1

    if not candidates:
        return None, {
            **debug_base,
            "attempts_used": 1,
            "picked_by": "quality_weighted" if pick_mode_raw == "weighted" else "quality_best",
            "candidates_drawn": int(drawn),
            "candidates_accepted": int(accepted),
            "quality_pick_mode": pick_mode_raw,
            "quality_temperature": float(temperature),
        }

    if pick_mode_raw == "best":
        picked = max(candidates, key=lambda x: x[3])
        picked_by = "quality_best"
    else:
        max_logit = max(x[3] for x in candidates)
        weights = [math.exp(float(x[3]) - float(max_logit)) for x in candidates]
        total = float(sum(weights))
        if not math.isfinite(total) or total <= 0.0:
            picked = max(candidates, key=lambda x: x[3])
            picked_by = "quality_best"
        else:
            r = float(rng.random()) * total
            idx = 0
            for i, w in enumerate(weights):
                r -= float(w)
                if r <= 0:
                    idx = i
                    break
            picked = candidates[int(max(0, min(idx, len(candidates) - 1)))]
            picked_by = "quality_weighted"

    best_image, best_score, best_multiplier, _best_logit, best_base, best_fresh, best_vel = picked

    return (
        best_image,
        {
            **debug_base,
            "attempts_used": 1,
            "picked_by": picked_by,
            "candidates_drawn": int(drawn),
            "candidates_accepted": int(accepted),
            "quality_pick_mode": pick_mode_raw,
            "quality_temperature": float(temperature),
            "quality_score": float(best_score),
            "quality_score_base": float(best_base),
            "quality_score_freshness": float(best_fresh),
            "quality_score_bookmark_velocity": float(best_vel),
            "quality_multiplier": float(best_multiplier),
        },
    )


def needs_opportunistic_hydrate(image: Any) -> bool:
    return (
        getattr(image, "width", None) is None
        or getattr(image, "height", None) is None
        or getattr(image, "x_restrict", None) is None
        or getattr(image, "ai_type", None) is None
        or getattr(image, "illust_type", None) is None
        or getattr(image, "user_id", None) is None
        or getattr(image, "bookmark_count", None) is None
        or getattr(image, "view_count", None) is None
        or getattr(image, "comment_count", None) is None
    )
