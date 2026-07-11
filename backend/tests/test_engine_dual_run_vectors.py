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


def test_dual_run_vector_merge_anti_repeat_excludes() -> None:
    from app.core.random_engine_pick import merge_engine_exclude_ids

    class _Ctx:
        anti_repeat_enabled = True
        recent_exclude_image_ids = [10, 11, 7]

    merged = merge_engine_exclude_ids(pick_ctx=_Ctx(), exclude_image_ids={7, 8})
    assert merged == {7, 8, 10, 11}

    class _Off:
        anti_repeat_enabled = False
        recent_exclude_image_ids = [99]

    assert merge_engine_exclude_ids(pick_ctx=_Off(), exclude_image_ids=[1]) == {1}


def test_dual_run_vector_client_dedup_key_and_feed_limit() -> None:
    filters = _parse(r18=0, seed="feed-seed")
    payload = compose_engine_pick_payload(
        r18=int(filters.r18),
        r18_strict=1,
        ai_type_raw=filters.ai_type_raw,
        ai_type_i=filters.ai_type_i,
        illust_type_i=filters.illust_type_i,
        orientation_code=filters.orientation_map[filters.layout_norm],
        min_width_i=0,
        min_height_i=0,
        min_pixels_i=0,
        min_bookmarks_i=0,
        min_views_i=0,
        min_comments_i=0,
        included=[],
        excluded=[],
        exclude_image_ids=[1, 2, 3],
        user_id=None,
        illust_id=None,
        created_from_norm=None,
        created_to_norm=None,
        fail_cooldown_before="2026-01-01T00:00:00.000Z",
        strategy_norm="random",
        quality_samples_i=12,
        pick_mode_raw="weighted",
        temperature=1.0,
        score_weights={},
        multipliers={},
        freshness_half_life_days=30.0,
        velocity_smooth_days=7.0,
        seed=filters.seed_norm,
        limit=20,
        client_dedup_key="bff-anti-repeat",
    )
    assert payload["limit"] == 20
    assert payload["client_dedup_key"] == "bff-anti-repeat"
    assert payload["filters"]["fail_cooldown_before"] == "2026-01-01T00:00:00.000Z"
    assert payload["filters"]["exclude_image_ids"] == [1, 2, 3]
    assert payload["seed"] == "feed-seed"
    assert "quality" not in payload


def _minimal_plan(*, anti_repeat_enabled: bool, recent_exclude: list[int] | None = None):
    from types import SimpleNamespace

    from app.core.random_pick_context import RandomPickContext

    return RandomPickContext(
        attempts=3,
        attempts_source="test",
        r18_strict=1,
        r18_strict_source="test",
        fail_cooldown_ms=0,
        fail_cooldown_source="test",
        fail_cooldown_before=None,
        strategy_norm="random",
        strategy_source="test",
        quality_samples_i=12,
        quality_samples_base=12,
        quality_samples_multiplier=1,
        quality_samples_scaled=False,
        quality_samples_source="test",
        pick_mode_raw="weighted",
        temperature=1.0,
        score_weights={},
        multipliers={},
        freshness_half_life_days=30.0,
        velocity_smooth_days=7.0,
        recommendation_source="test",
        rec_override_keys=[],
        time_boost_enabled=False,
        anti_repeat_enabled=anti_repeat_enabled,
        dedup_enabled_setting=anti_repeat_enabled,
        dedup_window_s=60.0,
        dedup_max_images=32,
        dedup_max_authors=8,
        dedup_strict=False,
        dedup_image_penalty=0.0,
        dedup_author_penalty=0.0,
        recent_image_ids=set(recent_exclude or []),
        recent_author_ids=set(),
        recent_exclude_image_ids=list(recent_exclude or []),
        pick_kwargs={},
        debug_base={},
        rng=SimpleNamespace(),
        seed_norm="",
    )


def test_dual_run_vector_plan_injects_bff_anti_repeat_dedup_key() -> None:
    filters = _parse()
    plan = _minimal_plan(anti_repeat_enabled=True, recent_exclude=[5])
    body = plan.build_engine_payload(filters=filters, exclude_image_ids=[5, 9], limit=1)
    assert body["client_dedup_key"] == "bff-anti-repeat"
    assert set(body["filters"]["exclude_image_ids"]) == {5, 9}

    plan_off = _minimal_plan(anti_repeat_enabled=False)
    body_off = plan_off.build_engine_payload(filters=filters, limit=1)
    assert "client_dedup_key" not in body_off
