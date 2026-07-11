from __future__ import annotations

import pytest

from app.core.errors import ApiError
from app.core.public_list_filters import parse_public_list_filters


def test_parse_public_list_filters_happy_path() -> None:
    f = parse_public_list_filters(
        limit=25,
        cursor="12",
        r18=0,
        r18_strict=1,
        ai_type="0",
        orientation="portrait",
        min_width=100,
        min_height=0,
        min_pixels=0,
        included_tags=["girl"],
        excluded_tags=None,
        user_id=None,
        illust_id=None,
        created_from=None,
        created_to=None,
    )
    assert f.limit == 25
    assert f.cursor_i == 12
    assert f.ai_type_i == 0
    assert f.orientation_code == 1
    assert f.included == ["girl"]


def test_parse_public_list_filters_rejects_bad_cursor() -> None:
    with pytest.raises(ApiError) as ei:
        parse_public_list_filters(
            limit=10,
            cursor="abc",
            r18=0,
            r18_strict=1,
            ai_type="any",
            orientation="any",
            min_width=0,
            min_height=0,
            min_pixels=0,
            included_tags=None,
            excluded_tags=None,
            user_id=None,
            illust_id=None,
            created_from=None,
            created_to=None,
        )
    assert ei.value.status_code == 400
