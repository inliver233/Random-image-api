from __future__ import annotations

from types import SimpleNamespace

from app.core.errors import ErrorCode
from app.core.random_query import no_match_error_from_filters
from app.core.random_request import parse_public_debug_flag
from app.core.random_response import resolve_public_item_urls


class _QP(dict):
    def get(self, key, default=None):  # noqa: ANN001
        return super().get(key, default)


def test_parse_public_debug_flag_truthy_and_default() -> None:
    assert parse_public_debug_flag(_QP()) is False
    assert parse_public_debug_flag(_QP(debug="0")) is False
    assert parse_public_debug_flag(_QP(debug="1")) is True
    assert parse_public_debug_flag(_QP(debug="true")) is True
    assert parse_public_debug_flag(_QP(debug="YES")) is True
    assert parse_public_debug_flag(_QP(debug="on")) is True
    assert parse_public_debug_flag(_QP(debug="no")) is False


def test_no_match_error_from_filters_shape() -> None:
    filters = SimpleNamespace(
        r18=0,
        ai_type_raw="any",
        illust_type_raw="any",
        adaptive=0,
        layout_norm="any",
        min_width_i=0,
        min_height_i=0,
        min_pixels_i=0,
        min_bookmarks_i=0,
        min_views_i=0,
        min_comments_i=0,
        included=[],
        excluded=[],
        user_id=None,
        illust_id=None,
        created_from_norm=None,
        created_to_norm=None,
        ai_type_i=None,
        illust_type_i=None,
    )
    err = no_match_error_from_filters(filters, r18_strict=1)
    assert err.code == ErrorCode.NO_MATCH
    assert err.status_code == 404
    hints = err.details["hints"]
    assert hints["applied_filters"]["r18"] == 0
    assert hints["applied_filters"]["r18_strict"] == 1
    assert isinstance(hints["suggestions"], list)
    assert hints["suggestions"]


def test_resolve_public_item_urls_local_fallback() -> None:
    image = SimpleNamespace(
        id=42,
        ext="jpg",
        original_url="https://i.pximg.net/img-original/img/2020/01/01/00/00/00/1_p0.jpg",
    )
    settings = SimpleNamespace(
        image_edge_enabled=False,
        image_edge_secret="",
        image_edge_base_urls=[],
        image_edge_sign_ttl_seconds=604800,
        image_edge_secret_previous="",
        imgproxy_enabled=False,
        imgproxy_key="",
        imgproxy_salt="",
        imgproxy_base_url="",
    )
    urls = resolve_public_item_urls(
        image=image,
        settings=settings,
        hide_origin=False,
        request_base_url="https://example.test/",
    )
    assert urls.local_url == "/i/42.jpg"
    assert urls.proxy_url == "/i/42.jpg"
    assert urls.origin_url == image.original_url
    assert urls.imgproxy_url is None

    hidden = resolve_public_item_urls(
        image=image,
        settings=settings,
        hide_origin=True,
        request_base_url="https://example.test/",
    )
    assert hidden.origin_url is None
    assert hidden.local_url == "/i/42.jpg"
