from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.errors import ApiError, ErrorCode
from app.core.time import iso_utc_ms

QUALITY_SAMPLES_MAX_QUERY = 200
QUALITY_SAMPLES_MAX_AUTO = 64
DEFAULT_ATTEMPTS = 3
DEFAULT_QUALITY_SAMPLES = 12
DEFAULT_STRATEGY = "quality"
DEFAULT_R18_STRICT = 1


@dataclass(frozen=True)
class ResolvedInt:
    value: int
    source: str  # query | runtime | fallback


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
