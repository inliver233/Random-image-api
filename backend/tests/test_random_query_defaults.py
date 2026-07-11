from __future__ import annotations

import pytest

from app.core.errors import ApiError
from app.core.random_defaults import (
    resolve_attempts,
    resolve_dedup,
    resolve_quality_samples,
    resolve_r18_strict,
    resolve_recommendation_config,
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


def test_resolve_dedup_runtime_override() -> None:
    cfg = resolve_dedup(
        {
            "dedup": {
                "enabled": False,
                "window_s": 120,
                "max_images": 10,
                "max_authors": 5,
                "strict": True,
                "image_penalty": 3.5,
                "author_penalty": 1.25,
            }
        }
    )
    assert cfg.enabled is False
    assert cfg.window_s == 120.0
    assert cfg.max_images == 10
    assert cfg.max_authors == 5
    assert cfg.strict is True
    assert cfg.image_penalty == 3.5
    assert cfg.author_penalty == 1.25


def test_resolve_recommendation_config_query_override() -> None:
    class QP(dict):
        def get(self, key, default=None):  # type: ignore[no-untyped-def]
            return super().get(key, default)

        def keys(self):  # type: ignore[no-untyped-def]
            return super().keys()

    qp = QP({"rec_pick_mode": "best", "rec_temperature": "2.5", "rec_w_bookmark": "9"})
    cfg = resolve_recommendation_config(
        random_defaults={"recommendation": {"pick_mode": "weighted", "temperature": 1.0}},
        query_params=qp,
    )
    assert cfg.source == "query"
    assert cfg.pick_mode == "best"
    assert cfg.temperature == 2.5
    assert cfg.score_weights["bookmark"] == 9.0
    assert "rec_pick_mode" in cfg.query_override_keys
