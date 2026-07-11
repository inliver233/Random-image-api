from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.errors import ApiError, ErrorCode
from app.core.recommendation import (
    DEFAULT_RECOMMENDATION,
    DEFAULT_SCORE_WEIGHTS,
    as_bool,
    as_float,
    parse_recommendation_overrides_from_query,
)
from app.core.time import iso_utc_ms

QUALITY_SAMPLES_MAX_QUERY = 200
QUALITY_SAMPLES_MAX_AUTO = 64
DEFAULT_ATTEMPTS = 3
DEFAULT_QUALITY_SAMPLES = 12
DEFAULT_STRATEGY = "quality"
DEFAULT_R18_STRICT = 1

DEFAULT_DEDUP: dict[str, Any] = {
    "enabled": True,
    "window_s": 20.0 * 60.0,
    "max_images": 5000,
    "max_authors": 2000,
    "strict": False,
    "image_penalty": 8.0,
    "author_penalty": 2.5,
}


@dataclass(frozen=True)
class ResolvedInt:
    value: int
    source: str  # query | runtime | fallback


@dataclass(frozen=True)
class DedupConfig:
    enabled: bool
    window_s: float
    max_images: int
    max_authors: int
    strict: bool
    image_penalty: float
    author_penalty: float


@dataclass(frozen=True)
class RecommendationConfig:
    source: str  # fallback | runtime | query
    query_override_keys: list[str]
    pick_mode: str
    temperature: float
    score_weights: dict[str, float]
    multipliers: dict[str, float]
    freshness_half_life_days: float
    velocity_smooth_days: float


def resolve_attempts(attempts: int | None, random_defaults: dict[str, Any]) -> ResolvedInt:
    source = "query"
    value = DEFAULT_ATTEMPTS
    if attempts is not None:
        try:
            value = int(attempts)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported attempts", status_code=400) from exc
    else:
        raw = random_defaults.get("default_attempts")
        if raw is None:
            source = "fallback"
            value = DEFAULT_ATTEMPTS
        else:
            source = "runtime"
            try:
                value = int(raw)
            except Exception:
                value = DEFAULT_ATTEMPTS
    if value < 1 or value > 10:
        if source == "query":
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported attempts", status_code=400)
        source = "fallback"
        value = DEFAULT_ATTEMPTS
    return ResolvedInt(value=int(value), source=source)


def resolve_r18_strict(r18_strict: int | None, random_defaults: dict[str, Any]) -> ResolvedInt:
    source = "query"
    value = DEFAULT_R18_STRICT
    if r18_strict is not None:
        try:
            value = int(r18_strict)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported r18_strict", status_code=400) from exc
    else:
        raw = random_defaults.get("default_r18_strict")
        if raw is None:
            source = "fallback"
            value = DEFAULT_R18_STRICT
        elif isinstance(raw, bool):
            source = "runtime"
            value = 1 if raw else 0
        else:
            source = "runtime"
            try:
                value = int(raw)
            except Exception:
                value = DEFAULT_R18_STRICT
    if value not in {0, 1}:
        if source == "query":
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported r18_strict", status_code=400)
        source = "fallback"
        value = DEFAULT_R18_STRICT
    return ResolvedInt(value=int(value), source=source)


def resolve_fail_cooldown_ms(random_defaults: dict[str, Any]) -> tuple[int, str, str | None]:
    """
    Returns (fail_cooldown_ms, source, fail_cooldown_before_iso_or_none).
    fail_cooldown_before is computed relative to now (UTC).
    """
    source = "runtime"
    fail_cooldown_ms = random_defaults.get("fail_cooldown_ms")
    try:
        fail_cooldown_ms_i = int(fail_cooldown_ms) if fail_cooldown_ms is not None else None
    except Exception:
        fail_cooldown_ms_i = None

    if fail_cooldown_ms_i is None:
        source = "fallback"
        cooldown_s_raw = (os.environ.get("RANDOM_FAIL_COOLDOWN_SECONDS") or "600").strip()
        try:
            cooldown_s = int(cooldown_s_raw)
        except Exception:
            cooldown_s = 600
        cooldown_s = max(0, min(int(cooldown_s), 24 * 60 * 60))
        fail_cooldown_ms_i = int(cooldown_s) * 1000
    fail_cooldown_ms_i = max(0, min(int(fail_cooldown_ms_i), 24 * 60 * 60 * 1000))

    request_now = datetime.now(timezone.utc)
    fail_cooldown_before = (
        iso_utc_ms(request_now - timedelta(milliseconds=int(fail_cooldown_ms_i)))
        if int(fail_cooldown_ms_i) > 0
        else None
    )
    return int(fail_cooldown_ms_i), source, fail_cooldown_before


def resolve_strategy(strategy: str | None, random_defaults: dict[str, Any]) -> tuple[str, str]:
    strategy_raw = (strategy or "").strip().lower()
    source = "query"
    if not strategy_raw:
        source = "runtime"
        strategy_raw = str(random_defaults.get("strategy") or "").strip().lower()
    if not strategy_raw:
        source = "fallback"
        strategy_raw = DEFAULT_STRATEGY
    if strategy_raw not in {"quality", "random"}:
        if source == "query":
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported strategy", status_code=400)
        source = "fallback"
        strategy_raw = DEFAULT_STRATEGY
    return strategy_raw, source


@dataclass(frozen=True)
class QualitySamplesPlan:
    samples: int
    base: int
    multiplier: int
    scaled: bool
    source: str


def resolve_quality_samples(
    *,
    quality_samples: int | None,
    random_defaults: dict[str, Any],
    strategy_norm: str,
    time_boost_enabled: bool,
    included: list[str],
    excluded: list[str],
    min_bookmarks_i: int,
    min_views_i: int,
    min_comments_i: int,
    min_pixels_i: int,
    min_width_i: int,
    min_height_i: int,
    ai_type_i: int | None,
    illust_type_i: int | None,
    orientation_set: bool,
    created_from_norm: str | None,
    created_to_norm: str | None,
    r18: int,
    anti_repeat_enabled: bool,
) -> QualitySamplesPlan:
    source = "query"
    if quality_samples is not None:
        try:
            samples_i = int(quality_samples)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported quality_samples", status_code=400) from exc
    else:
        raw = random_defaults.get("quality_samples")
        if raw is None:
            source = "fallback"
            samples_i = DEFAULT_QUALITY_SAMPLES
        else:
            source = "runtime"
            try:
                samples_i = int(raw)
            except Exception:
                samples_i = DEFAULT_QUALITY_SAMPLES

    if samples_i < 1 or samples_i > QUALITY_SAMPLES_MAX_QUERY:
        if source == "query":
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported quality_samples", status_code=400)
        source = "fallback"
        samples_i = DEFAULT_QUALITY_SAMPLES
    elif source != "query" and samples_i > QUALITY_SAMPLES_MAX_AUTO:
        samples_i = QUALITY_SAMPLES_MAX_AUTO

    base = int(samples_i)
    multiplier = 1
    if quality_samples is None and strategy_norm == "quality" and bool(time_boost_enabled):
        strictness = 0
        strictness += 3 * int(len(included))
        strictness += 2 * int(len(excluded))
        if int(min_bookmarks_i) > 0:
            strictness += 2
        if int(min_views_i) > 0:
            strictness += 1
        if int(min_comments_i) > 0:
            strictness += 1
        if int(min_pixels_i) > 0:
            strictness += 1
        if int(min_width_i) > 0 or int(min_height_i) > 0:
            strictness += 1
        if ai_type_i is not None:
            strictness += 1
        if illust_type_i is not None:
            strictness += 1
        if orientation_set:
            strictness += 1
        if created_from_norm is not None or created_to_norm is not None:
            strictness += 1
        if int(r18) == 1:
            strictness += 1
        if bool(anti_repeat_enabled):
            strictness += 1

        if strictness >= 9:
            multiplier = 4
        elif strictness >= 6:
            multiplier = 3
        elif strictness >= 3:
            multiplier = 2
        else:
            multiplier = 1

        samples_i = min(
            QUALITY_SAMPLES_MAX_AUTO,
            int(max(1, int(base) * int(multiplier))),
        )

    scaled = bool(samples_i != base)
    return QualitySamplesPlan(
        samples=int(samples_i),
        base=int(base),
        multiplier=int(multiplier),
        scaled=bool(scaled),
        source=source,
    )


def resolve_dedup(random_defaults: dict[str, Any]) -> DedupConfig:
    enabled = bool(DEFAULT_DEDUP["enabled"])
    window_s = float(DEFAULT_DEDUP["window_s"])
    max_images = int(DEFAULT_DEDUP["max_images"])
    max_authors = int(DEFAULT_DEDUP["max_authors"])
    strict = bool(DEFAULT_DEDUP["strict"])
    image_penalty = float(DEFAULT_DEDUP["image_penalty"])
    author_penalty = float(DEFAULT_DEDUP["author_penalty"])

    dedup_raw = random_defaults.get("dedup")
    if isinstance(dedup_raw, dict):
        v = as_bool(dedup_raw.get("enabled"))
        if v is not None:
            enabled = bool(v)

        window_raw = dedup_raw.get("window_s")
        if window_raw is not None:
            try:
                window_s = float(max(0.0, min(float(window_raw), 24.0 * 60.0 * 60.0)))
            except Exception:
                pass

        max_images_raw = dedup_raw.get("max_images")
        if max_images_raw is not None:
            try:
                max_images = int(max(1, min(int(max_images_raw), 200_000)))
            except Exception:
                pass

        max_authors_raw = dedup_raw.get("max_authors")
        if max_authors_raw is not None:
            try:
                max_authors = int(max(1, min(int(max_authors_raw), 200_000)))
            except Exception:
                pass

        v = as_bool(dedup_raw.get("strict"))
        if v is not None:
            strict = bool(v)

        image_pen_raw = dedup_raw.get("image_penalty")
        if image_pen_raw is not None:
            try:
                v_f = float(image_pen_raw)
                if math.isfinite(v_f):
                    image_penalty = float(max(0.0, min(v_f, 1000.0)))
            except Exception:
                pass

        author_pen_raw = dedup_raw.get("author_penalty")
        if author_pen_raw is not None:
            try:
                v_f = float(author_pen_raw)
                if math.isfinite(v_f):
                    author_penalty = float(max(0.0, min(v_f, 1000.0)))
            except Exception:
                pass

    return DedupConfig(
        enabled=bool(enabled),
        window_s=float(window_s),
        max_images=int(max_images),
        max_authors=int(max_authors),
        strict=bool(strict),
        image_penalty=float(image_penalty),
        author_penalty=float(author_penalty),
    )


def resolve_recommendation_config(
    *,
    random_defaults: dict[str, Any],
    query_params: Any = None,
) -> RecommendationConfig:
    recommendation_raw = random_defaults.get("recommendation")
    source = "fallback"
    recommendation_obj: dict[str, Any] = {}
    if isinstance(recommendation_raw, dict):
        source = "runtime"
        recommendation_obj = dict(recommendation_raw)

    rec_overrides, rec_override_keys = parse_recommendation_overrides_from_query(query_params)
    if rec_overrides:
        source = "query"
        recommendation_obj = dict(recommendation_obj)
        for k, v in rec_overrides.items():
            if k in {"score_weights", "multipliers"}:
                base_raw = recommendation_obj.get(k)
                base = dict(base_raw) if isinstance(base_raw, dict) else {}
                if isinstance(v, dict):
                    base.update(v)
                recommendation_obj[k] = base
            else:
                recommendation_obj[k] = v

    pick_mode_raw = str(recommendation_obj.get("pick_mode") or DEFAULT_RECOMMENDATION["pick_mode"]).strip().lower()
    if pick_mode_raw not in {"best", "weighted"}:
        pick_mode_raw = str(DEFAULT_RECOMMENDATION["pick_mode"])

    temperature_raw = as_float(recommendation_obj.get("temperature"), default=float(DEFAULT_RECOMMENDATION["temperature"]))
    temperature = float(max(0.05, min(float(temperature_raw), 100.0)))

    score_weights_raw = recommendation_obj.get("score_weights")
    score_weights_obj = score_weights_raw if isinstance(score_weights_raw, dict) else {}
    score_weights: dict[str, float] = {}
    for key, default_value in DEFAULT_SCORE_WEIGHTS.items():
        v = as_float(score_weights_obj.get(key), default=float(default_value))
        score_weights[key] = float(max(-100.0, min(float(v), 100.0)))

    multipliers_default = DEFAULT_RECOMMENDATION["multipliers"]
    multipliers_raw = recommendation_obj.get("multipliers")
    multipliers_obj = multipliers_raw if isinstance(multipliers_raw, dict) else {}
    multipliers: dict[str, float] = {}
    for key, default_value in multipliers_default.items():
        v = as_float(multipliers_obj.get(key), default=float(default_value))
        multipliers[key] = float(max(0.0, min(float(v), 100.0)))

    freshness_half_life_days = float(DEFAULT_RECOMMENDATION["freshness_half_life_days"])
    if "freshness_half_life_days" in recommendation_obj:
        v = as_float(recommendation_obj.get("freshness_half_life_days"), default=float("nan"))
        if math.isfinite(float(v)):
            freshness_half_life_days = float(max(0.1, min(float(v), 3650.0)))

    velocity_smooth_days = float(DEFAULT_RECOMMENDATION["velocity_smooth_days"])
    if "velocity_smooth_days" in recommendation_obj:
        v = as_float(recommendation_obj.get("velocity_smooth_days"), default=float("nan"))
        if math.isfinite(float(v)):
            velocity_smooth_days = float(max(0.0, min(float(v), 3650.0)))

    return RecommendationConfig(
        source=source,
        query_override_keys=list(rec_override_keys or []),
        pick_mode=str(pick_mode_raw),
        temperature=float(temperature),
        score_weights=score_weights,
        multipliers=multipliers,
        freshness_half_life_days=float(freshness_half_life_days),
        velocity_smooth_days=float(velocity_smooth_days),
    )
