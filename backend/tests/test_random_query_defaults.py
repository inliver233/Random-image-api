from __future__ import annotations

import pytest

from app.core.errors import ApiError
from app.core.random_defaults import (
    resolve_attempts,
    resolve_quality_samples,
    resolve_r18_strict,
    resolve_strategy,
)
from app.core.random_query import normalize_iso_utc, parse_tag_filters, validate_tag_filters


def test_parse_tag_filters_dedupes() -> None:
    assert parse_tag_filters([" girl ", "boy", "girl", "", "  "]) == ["girl", "boy"]


def test_validate_tag_filters_or_limit() -> None:
    expr = "|".join(f"t{i}" for i in range(21))
    with pytest.raises(ApiError) as ei:
        validate_tag_filters([expr])
    assert ei.value.status_code == 400


def test_normalize_iso_utc_z() -> None:
    assert normalize_iso_utc("2024-01-02T03:04:05Z") == "2024-01-02T03:04:05Z"


def test_resolve_attempts_bounds() -> None:
    assert resolve_attempts(None, {}).value == 3
    assert resolve_attempts(5, {}).value == 5
    with pytest.raises(ApiError):
        resolve_attempts(0, {})
    assert resolve_attempts(None, {"default_attempts": 99}).value == 3  # fallback clamp


def test_resolve_r18_strict_bool_runtime() -> None:
    assert resolve_r18_strict(None, {"default_r18_strict": False}).value == 0
    assert resolve_r18_strict(None, {"default_r18_strict": True}).value == 1


def test_resolve_strategy_default() -> None:
    s, src = resolve_strategy(None, {})
    assert s == "quality"
    assert src == "fallback"


def test_resolve_quality_samples_hard_cap() -> None:
    with pytest.raises(ApiError):
        resolve_quality_samples(
            quality_samples=201,
            random_defaults={},
            strategy_norm="quality",
            time_boost_enabled=True,
            included=[],
            excluded=[],
            min_bookmarks_i=0,
            min_views_i=0,
            min_comments_i=0,
            min_pixels_i=0,
            min_width_i=0,
            min_height_i=0,
            ai_type_i=None,
            illust_type_i=None,
            orientation_set=False,
            created_from_norm=None,
            created_to_norm=None,
            r18=0,
            anti_repeat_enabled=False,
        )
