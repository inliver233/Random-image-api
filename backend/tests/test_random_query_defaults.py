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
from app.core.random_engine_pick import (
    build_engine_filters,
    build_engine_quality_params,
    orientation_to_engine_str,
)
from app.core.random_query import normalize_iso_utc, parse_tag_filters, validate_tag_filters
from app.core.random_request import parse_random_filters, prefer_image_edge
from app.core.random_response import build_json_body, build_simple_json_body


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


def test_parse_random_filters_adaptive_mobile() -> None:
    f = parse_random_filters(
        format="json",
        redirect=0,
        seed=None,
        r18=0,
        ai_type="any",
        illust_type="any",
        orientation="any",
        layout=None,
        adaptive=1,
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
        headers={"sec-ch-ua-mobile": "?1"},
    )
    assert f.layout_norm == "portrait"
    assert f.min_pixels_i == 1_000_000


def test_prefer_image_edge_escape_hatches() -> None:
    assert prefer_image_edge(proxy_override=None, pixiv_cat=0, pximg_mirror_host_override=None) is True
    assert prefer_image_edge(proxy_override="i.pixiv.cat", pixiv_cat=0, pximg_mirror_host_override=None) is False
    assert prefer_image_edge(proxy_override=None, pixiv_cat=1, pximg_mirror_host_override=None) is False
    assert prefer_image_edge(proxy_override=None, pixiv_cat=0, pximg_mirror_host_override="i.pixiv.re") is False
    assert prefer_image_edge(proxy_override=None, pixiv_cat=0, pximg_mirror_host_override=None, force_local=True) is False


def test_build_engine_filters_and_quality() -> None:
    assert orientation_to_engine_str(1) == "portrait"
    filters = build_engine_filters(
        r18=0,
        r18_strict=1,
        ai_type_raw="any",
        ai_type_i=None,
        illust_type_i=None,
        orientation_code=2,
        min_width_i=0,
        min_height_i=0,
        min_pixels_i=0,
        min_bookmarks_i=0,
        min_views_i=0,
        min_comments_i=0,
        included=["a"],
        excluded=[],
        exclude_image_ids={1, 2},
        user_id=9,
    )
    assert filters["orientation"] == "landscape"
    assert filters["user_id"] == 9
    assert set(filters["exclude_image_ids"]) == {1, 2}
    assert build_engine_quality_params(
        strategy_norm="random",
        quality_samples_i=8,
        pick_mode_raw="weighted",
        temperature=1.0,
        score_weights={},
        multipliers={},
        freshness_half_life_days=30.0,
        velocity_smooth_days=7.0,
    ) is None
    q = build_engine_quality_params(
        strategy_norm="quality",
        quality_samples_i=8,
        pick_mode_raw="weighted",
        temperature=1.0,
        score_weights={"bookmark": 1.0},
        multipliers={},
        freshness_half_life_days=30.0,
        velocity_smooth_days=7.0,
    )
    assert q is not None
    assert q["samples"] == 8


class _Img:
    id = 1
    illust_id = 100
    page_index = 0
    ext = "jpg"
    width = 100
    height = 200
    x_restrict = 0
    ai_type = 0
    illust_type = 0
    bookmark_count = 1
    view_count = 2
    comment_count = 3
    user_id = 9
    user_name = "u"
    title = "t"
    created_at_pixiv = "2020-01-01T00:00:00Z"


def test_build_json_bodies() -> None:
    img = _Img()
    simple = build_simple_json_body(
        request_id="r1",
        image=img,
        proxy_url="/i/1.jpg",
        origin_url=None,
        imgproxy_url=None,
        debug={"a": 1},
    )
    assert simple["ok"] is True
    assert simple["data"]["urls"]["proxy"] == "/i/1.jpg"
    assert "tags" not in simple["data"]
    full = build_json_body(
        request_id="r1",
        image=img,
        tags=["x"],
        proxy_url="/i/1.jpg",
        origin_url="https://i.pximg.net/x.jpg",
        imgproxy_url=None,
        debug={},
    )
    assert full["data"]["tags"] == ["x"]
    assert full["data"]["image"]["title"] == "t"
