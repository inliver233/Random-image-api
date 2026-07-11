from __future__ import annotations

import pytest

from app.core.admin_request import (
    parse_bool,
    parse_bool_optional,
    parse_int_in_range,
    parse_optional_str,
    parse_positive_int,
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
    with pytest.raises(ApiError) as ei_custom:
        parse_optional_str("too-long", max_len=3, field="reason", invalid_message="Invalid reason")
    assert ei_custom.value.message == "Invalid reason"


def test_parse_positive_int() -> None:
    assert parse_positive_int(3, field="pool_id") == 3
    assert parse_positive_int("7", field="pool_id") == 7
    with pytest.raises(ApiError) as ei_zero:
        parse_positive_int(0, field="pool_id")
    assert ei_zero.value.message == "Invalid pool_id"
    with pytest.raises(ApiError) as ei_bad:
        parse_positive_int("x", field="illust_id")
    assert ei_bad.value.message == "Invalid illust_id"
    with pytest.raises(ApiError) as ei_custom:
        parse_positive_int(-1, field="id", invalid_message="bad id")
    assert ei_custom.value.message == "bad id"


def test_parse_int_in_range() -> None:
    assert parse_int_in_range(0, field="keep_days", min_value=0, max_value=36500) == 0
    assert parse_int_in_range("12", field="chunk_size", min_value=1, max_value=100) == 12
    with pytest.raises(ApiError) as ei_low:
        parse_int_in_range(-1, field="keep_days", min_value=0, max_value=10)
    assert ei_low.value.message == "Unsupported keep_days"
    with pytest.raises(ApiError) as ei_high:
        parse_int_in_range(101, field="chunk_size", min_value=1, max_value=100)
    assert ei_high.value.message == "Unsupported chunk_size"


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
