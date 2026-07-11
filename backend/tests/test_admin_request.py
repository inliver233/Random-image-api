from __future__ import annotations

import pytest

from app.core.admin_request import (
    parse_bool,
    parse_bool_optional,
    parse_optional_str,
    parse_positive_int_list,
)
from app.core.errors import ApiError


def test_parse_bool_defaults() -> None:
    assert parse_bool(None, default=True) is True
    assert parse_bool("yes", default=False) is True
    assert parse_bool("off", default=True) is False
    assert parse_bool("nope", default=True) is True


def test_parse_bool_optional() -> None:
    assert parse_bool_optional(None) is None
    assert parse_bool_optional(1) is True
    assert parse_bool_optional("false") is False
    assert parse_bool_optional("maybe") is None


def test_parse_optional_str() -> None:
    assert parse_optional_str(None) is None
    assert parse_optional_str("  ") is None
    assert parse_optional_str("  hello ") == "hello"
    assert parse_optional_str("ok", max_len=2, field="label") == "ok"
    with pytest.raises(ApiError) as ei:
        parse_optional_str("too-long", max_len=3, field="label")
    assert ei.value.message == "Unsupported label"
    assert ei.value.status_code == 400


def test_parse_positive_int_list() -> None:
    assert parse_positive_int_list([1, "2", 2, 0, -3, 3], field="image_ids") == [1, 2, 3]
    assert parse_positive_int_list([], field="image_ids", allow_empty=True) == []

    with pytest.raises(ApiError) as ei_empty:
        parse_positive_int_list([], field="image_ids")
    assert ei_empty.value.message == "Empty image_ids"

    with pytest.raises(ApiError) as ei_type:
        parse_positive_int_list("1,2", field="image_ids")
    assert ei_type.value.message == "Unsupported image_ids"

    with pytest.raises(ApiError) as ei_item:
        parse_positive_int_list([1, "x"], field="image_ids")
    assert ei_item.value.message == "Unsupported image_ids"

    with pytest.raises(ApiError) as ei_max:
        parse_positive_int_list([1, 2, 3], field="image_ids", max_items=2)
    assert ei_max.value.message == "Too many image_ids"

    with pytest.raises(ApiError) as ei_custom:
        parse_positive_int_list("nope", field="ids", invalid_message="bad ids")
    assert ei_custom.value.message == "bad ids"
