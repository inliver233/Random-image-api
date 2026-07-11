from __future__ import annotations

from app.core.random_defaults import build_pick_kwargs
from app.core.random_engine_pick import compose_engine_pick_payload
from app.core.random_request import parse_random_filters


def _parse(**overrides):
    base = dict(
        format="json",
        redirect=0,
        seed=None,
        r18=0,
        ai_type="any",
        illust_type="any",
        orientation="any",
        layout=None,
        adaptive=0,
        pixiv_cat=0,
        pximg_mirror_host=None,
        min_width=0,
        min_height=0,
        min_pixels=0,
        min_bookmarks=0,
        min_views=0,
        min_comments=0,
        included_tags=None,
        excluded_tags=None,
        user_id=None,
        illust_id=None,
        created_from=None,
        created_to=None,
        query_params={},
        headers={},
    )
    base.update(overrides)
    return parse_random_filters(**base)


def test_dual_run_vector_default_safe_filters() -> None:
    """Freeze default public filter → engine payload mapping for dual-run parity."""
    filters = _parse()
    pick_kw = build_pick_kwargs(
        r18=int(filters.r18),
        r18_strict=1,
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
        fail_cooldown_before=None,
    )
    payload = compose_engine_pick_payload(
        r18=int(filters.r18),
        r18_strict=1,
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
        exclude_image_ids=[],
        user_id=filters.user_id,
        illust_id=filters.illust_id,
        created_from_norm=filters.created_from_norm,
        created_to_norm=filters.created_to_norm,
        fail_cooldown_before=None,
        strategy_norm="random",
        quality_samples_i=12,
        pick_mode_raw="weighted",
        temperature=1.0,
        score_weights={},
        multipliers={},
        freshness_half_life_days=30.0,
        velocity_smooth_days=7.0,
        seed=None,
        limit=1,
    )
    eng = payload["filters"]
    assert pick_kw["r18"] == eng["r18"] == 0
    assert pick_kw["r18_strict"] is True
    assert eng["r18_strict"] == 1
    assert eng["ai_type"] == "any"
    assert eng["illust_type"] == "any"
    assert eng["orientation"] == "any"
    assert eng["min_width"] == pick_kw["min_width"] == 0
    assert eng["included_tags"] == pick_kw["included_tags"] == []
    assert payload["strategy"] == "random"
    assert "quality" not in payload


def test_dual_run_vector_quality_with_filters() -> None:
    filters = _parse(
        r18=2,
        ai_type="0",
        illust_type="manga",
        orientation="portrait",
        min_bookmarks=50,
        min_views=100,
        min_width=800,
        included_tags=["cat", "girl|boy"],
        excluded_tags=["ai"],
        user_id=42,
        created_from="2024-01-01T00:00:00Z",
        created_to="2024-12-31T23:59:59Z",
        seed="dual-run-seed",
    )
    payload = compose_engine_pick_payload(
        r18=int(filters.r18),
        r18_strict=0,
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
        exclude_image_ids={7, 8},
        user_id=filters.user_id,
        illust_id=filters.illust_id,
        created_from_norm=filters.created_from_norm,
        created_to_norm=filters.created_to_norm,
        fail_cooldown_before="2024-06-01T00:00:00.000Z",
        strategy_norm="quality",
        quality_samples_i=16,
        pick_mode_raw="weighted",
        temperature=0.8,
        score_weights={"bookmark": 1.0, "view": 0.5},
        multipliers={"ai": 0.0, "non_ai": 1.0},
        freshness_half_life_days=14.0,
        velocity_smooth_days=3.0,
        seed=filters.seed_norm,
        limit=12,
        debug=False,
    )
    eng = payload["filters"]
    assert eng["r18"] == 2
    assert eng["r18_strict"] == 0
    assert eng["ai_type"] == "0"
    assert eng["illust_type"] == "1"
    assert eng["orientation"] == "portrait"
    assert eng["min_width"] == 800
    assert eng["min_bookmarks"] == 50
    assert eng["min_views"] == 100
    assert eng["included_tags"] == ["cat", "girl|boy"]
    assert eng["excluded_tags"] == ["ai"]
    assert eng["user_id"] == 42
    assert eng["created_from"] == "2024-01-01T00:00:00Z"
    assert eng["created_to"] == "2024-12-31T23:59:59Z"
    assert eng["fail_cooldown_before"] == "2024-06-01T00:00:00.000Z"
    assert set(eng["exclude_image_ids"]) == {7, 8}
    assert payload["strategy"] == "quality"
    assert payload["limit"] == 12
    assert payload["seed"] == "dual-run-seed"
    assert payload["quality"]["samples"] == 16
    assert payload["quality"]["temperature"] == 0.8
    assert payload["quality"]["weights"]["bookmark"] == 1.0
