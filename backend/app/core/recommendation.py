from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.core.errors import ApiError, ErrorCode

DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "bookmark": 4.0,
    "view": 0.5,
    "comment": 2.0,
    "pixels": 1.0,
    "bookmark_rate": 3.0,
    # 适度提升“新鲜感”和“成长速度”，避免老热门长期统治。
    # - freshness: 越新越加分（指数衰减，默认半衰期在代码里固定为 21 天）
    # - bookmark_velocity: 收藏增长率（收藏数 / 存在天数）的对数项
    "freshness": 1.0,
    "bookmark_velocity": 1.2,
}

DEFAULT_RECOMMENDATION: dict[str, Any] = {
    "pick_mode": "weighted",
    "temperature": 1.0,
    "score_weights": dict(DEFAULT_SCORE_WEIGHTS),
    # Newness/trending knobs:
    # - freshness_half_life_days: used by the freshness decay term (see scoring).
    # - velocity_smooth_days: smoothing for bookmark velocity denominator (age_days + smooth).
    "freshness_half_life_days": 21.0,
    "velocity_smooth_days": 2.0,
    "multipliers": {
        "ai": 1.0,
        "non_ai": 1.0,
        "unknown_ai": 1.0,
        "illust": 1.0,
        "manga": 1.0,
        "ugoira": 1.0,
        "unknown_illust_type": 1.0,
    },
}


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "y", "on"}:
            return True
        if v in {"false", "0", "no", "n", "off"}:
            return False
    return None


def as_nonneg_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    try:
        i = int(value)
    except Exception:
        return 0
    return i if i > 0 else 0


def as_float(value: Any, *, default: float) -> float:
    if value is None:
        return float(default)
    if isinstance(value, bool):
        return float(default)
    try:
        return float(value)
    except Exception:
        return float(default)


def parse_iso_dt(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def age_days(dt: datetime | None, *, now: datetime | None = None) -> float | None:
    if dt is None:
        return None
    try:
        now_dt = now or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        delta = now_dt.astimezone(timezone.utc) - dt.astimezone(timezone.utc)
        days = float(delta.total_seconds()) / 86400.0
        if not math.isfinite(days):
            return None
        return float(max(0.0, days))
    except Exception:
        return None


def quality_score(image: Any, *, weights: dict[str, float] | None = None) -> float:
    w = weights or DEFAULT_SCORE_WEIGHTS
    w_bookmark = float(w.get("bookmark", DEFAULT_SCORE_WEIGHTS["bookmark"]))
    w_view = float(w.get("view", DEFAULT_SCORE_WEIGHTS["view"]))
    w_comment = float(w.get("comment", DEFAULT_SCORE_WEIGHTS["comment"]))
    w_pixels = float(w.get("pixels", DEFAULT_SCORE_WEIGHTS["pixels"]))
    w_bookmark_rate = float(w.get("bookmark_rate", DEFAULT_SCORE_WEIGHTS["bookmark_rate"]))

    bookmark_count = as_nonneg_int(getattr(image, "bookmark_count", None))
    view_count = as_nonneg_int(getattr(image, "view_count", None))
    comment_count = as_nonneg_int(getattr(image, "comment_count", None))

    width = as_nonneg_int(getattr(image, "width", None))
    height = as_nonneg_int(getattr(image, "height", None))
    pixels = width * height if width > 0 and height > 0 else 0

    rate_term = 0.0
    if view_count > 0:
        bookmark_rate_per_mille = (float(bookmark_count) / float(view_count)) * 1000.0
        rate_term = math.log1p(max(0.0, bookmark_rate_per_mille))

    score = (
        float(w_bookmark) * math.log1p(bookmark_count)
        + float(w_view) * math.log1p(view_count)
        + float(w_comment) * math.log1p(comment_count)
        + float(w_pixels) * math.log1p(float(pixels) / 1_000_000.0)
        + float(w_bookmark_rate) * rate_term
    )
    return float(score)


def parse_recommendation_overrides_from_query(query_params: Any) -> tuple[dict[str, Any], list[str]]:
    qp = query_params or {}
    used: list[str] = []
    overrides: dict[str, Any] = {}

    def _get_str(name: str) -> str | None:
        try:
            raw = qp.get(name)
        except Exception:
            raw = None
        if raw is None:
            return None
        s = str(raw).strip()
        if not s:
            return None
        used.append(name)
        return s

    def _get_float(name: str) -> float | None:
        try:
            raw = qp.get(name)
        except Exception:
            raw = None
        if raw is None:
            return None
        v = as_float(raw, default=float("nan"))
        if not math.isfinite(float(v)):
            raise ApiError(code=ErrorCode.BAD_REQUEST, message=f"Unsupported {name}", status_code=400)
        used.append(name)
        return float(v)

    # Be strict for rec_* prefixes to avoid silent typos.
    try:
        keys = list(qp.keys())
    except Exception:
        keys = []
    for k in keys:
        if not isinstance(k, str):
            continue
        if k.startswith("rec_w_"):
            weight_key = k.removeprefix("rec_w_")
            if weight_key not in DEFAULT_SCORE_WEIGHTS:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message=f"Unsupported {k}", status_code=400)
        if k.startswith("rec_m_"):
            mult_key = k.removeprefix("rec_m_")
            if mult_key not in DEFAULT_RECOMMENDATION["multipliers"]:
                raise ApiError(code=ErrorCode.BAD_REQUEST, message=f"Unsupported {k}", status_code=400)

    pick_mode_s = _get_str("rec_pick_mode")
    if pick_mode_s is not None:
        candidate = pick_mode_s.strip().lower()
        if candidate not in {"best", "weighted"}:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message="Unsupported rec_pick_mode", status_code=400)
        overrides["pick_mode"] = candidate

    temperature_v = _get_float("rec_temperature")
    if temperature_v is not None:
        overrides["temperature"] = float(max(0.05, min(float(temperature_v), 100.0)))

    freshness_half_life_v = _get_float("rec_fresh_half_life_days")
    if freshness_half_life_v is not None:
        overrides["freshness_half_life_days"] = float(max(0.1, min(float(freshness_half_life_v), 3650.0)))

    velocity_smooth_v = _get_float("rec_velocity_smooth_days")
    if velocity_smooth_v is not None:
        overrides["velocity_smooth_days"] = float(max(0.0, min(float(velocity_smooth_v), 3650.0)))

    score_overrides: dict[str, float] = {}
    for key in DEFAULT_SCORE_WEIGHTS.keys():
        v = _get_float(f"rec_w_{key}")
        if v is None:
            continue
        score_overrides[key] = float(max(-100.0, min(float(v), 100.0)))
    if score_overrides:
        overrides["score_weights"] = score_overrides

    mult_overrides: dict[str, float] = {}
    for key in DEFAULT_RECOMMENDATION["multipliers"].keys():
        v = _get_float(f"rec_m_{key}")
        if v is None:
            continue
        mult_overrides[key] = float(max(0.0, min(float(v), 100.0)))
    if mult_overrides:
        overrides["multipliers"] = mult_overrides

    return overrides, used


def multiplier_for_image(image: Any, *, multipliers: dict[str, float]) -> float:
    m = 1.0

    ai = getattr(image, "ai_type", None)
    if ai == 1:
        m *= float(multipliers.get("ai", 1.0))
    elif ai == 0:
        m *= float(multipliers.get("non_ai", 1.0))
    else:
        m *= float(multipliers.get("unknown_ai", 1.0))

    it = getattr(image, "illust_type", None)
    if it == 0:
        m *= float(multipliers.get("illust", 1.0))
    elif it == 1:
        m *= float(multipliers.get("manga", 1.0))
    elif it == 2:
        m *= float(multipliers.get("ugoira", 1.0))
    else:
        m *= float(multipliers.get("unknown_illust_type", 1.0))

    if not math.isfinite(float(m)) or float(m) <= 0.0:
        return 0.0
    return float(m)


def score_image_with_time_boosts(
    image: Any,
    *,
    score_weights: dict[str, float],
    freshness_half_life_days: float,
    velocity_smooth_days: float,
    time_boost_enabled: bool,
) -> tuple[float, float, float, float]:
    """
    Returns (score_total, score_base, freshness_contrib, velocity_contrib).
    """
    score_base = quality_score(image, weights=score_weights)

    freshness_w = float(score_weights.get("freshness", 0.0)) if bool(time_boost_enabled) else 0.0
    velocity_w = float(score_weights.get("bookmark_velocity", 0.0)) if bool(time_boost_enabled) else 0.0
    freshness_contrib = 0.0
    velocity_contrib = 0.0

    if freshness_w != 0.0:
        dt_created = parse_iso_dt(getattr(image, "created_at_pixiv", None))
        if dt_created is None:
            dt_created = parse_iso_dt(getattr(image, "added_at", None))
        days = age_days(dt_created)
        if days is not None:
            try:
                # log(decay) = - age/half_life, i.e. a true time-decay loss factor.
                freshness_contrib = (-1.0) * float(freshness_w) * (float(days) / float(freshness_half_life_days))
            except Exception:
                freshness_contrib = 0.0

    if velocity_w != 0.0:
        dt_created = parse_iso_dt(getattr(image, "created_at_pixiv", None))
        days = age_days(dt_created)
        bookmark_count = as_nonneg_int(getattr(image, "bookmark_count", None))
        if days is not None and bookmark_count > 0:
            try:
                # 平滑，避免“刚发布/刚补全”的极端值。
                denom = float(days) + float(velocity_smooth_days)
                velocity_term = math.log1p(float(bookmark_count) / max(1.0, denom))
                velocity_contrib = float(velocity_w) * float(velocity_term)
            except Exception:
                velocity_contrib = 0.0

    score = float(score_base) + float(freshness_contrib) + float(velocity_contrib)
    return float(score), float(score_base), float(freshness_contrib), float(velocity_contrib)
