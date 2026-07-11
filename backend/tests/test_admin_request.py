from __future__ import annotations

import pytest

from app.core.admin_request import (
    load_json_object_optional,
    parse_bool,
    parse_bool_optional,
    parse_choice,
    parse_float_in_range,
    parse_int_in_range,
    parse_optional_str,
    parse_positive_int,
    parse_positive_int_list,
    parse_required_str,
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


def test_parse_required_str() -> None:
    assert parse_required_str("  name ", field="name", max_len=10) == "name"
    with pytest.raises(ApiError) as ei_missing:
        parse_required_str("  ", field="name")
    assert ei_missing.value.message == "Missing name"
    with pytest.raises(ApiError) as ei_long:
        parse_required_str("toolong", field="name", max_len=3)
    assert ei_long.value.message == "Unsupported name"
    with pytest.raises(ApiError) as ei_custom:
        parse_required_str("", field="name", missing_message="Invalid name")
    assert ei_custom.value.message == "Invalid name"


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


def test_parse_float_in_range() -> None:
    assert parse_float_in_range(1.0, field="weight", min_value=0.0, max_value=100.0) == 1.0
    assert parse_float_in_range("2.5", field="weight", min_value=0.0, max_value=100.0) == 2.5
    with pytest.raises(ApiError) as ei_low:
        parse_float_in_range(-0.1, field="weight", min_value=0.0, max_value=100.0)
    assert ei_low.value.message == "Unsupported weight"
    with pytest.raises(ApiError) as ei_high:
        parse_float_in_range(100.1, field="weight", min_value=0.0, max_value=100.0)
    assert ei_high.value.message == "Unsupported weight"
    with pytest.raises(ApiError) as ei_bad:
        parse_float_in_range("x", field="weight", min_value=0.0, max_value=100.0)
    assert ei_bad.value.message == "Unsupported weight"


def test_parse_choice() -> None:
    assert parse_choice(None, field="conflict_policy", choices={"skip", "overwrite"}, default="skip") == "skip"
    assert parse_choice(" OVERWRITE ", field="conflict_policy", choices={"skip", "overwrite"}, default="skip") == "overwrite"
    with pytest.raises(ApiError) as ei:
        parse_choice("merge", field="conflict_policy", choices={"skip", "overwrite"}, default="skip")
    assert ei.value.message == "Unsupported conflict_policy"
    with pytest.raises(ApiError) as ei_missing:
        parse_choice("", field="status", choices={"a", "b"})
    assert ei_missing.value.message == "Unsupported status"


def test_load_json_object_optional() -> None:
    import asyncio

    class _Req:
        def __init__(self, payload: object) -> None:
            self._payload = payload

        async def json(self) -> object:
            if isinstance(self._payload, BaseException):
                raise self._payload
            return self._payload

    async def _run() -> None:
        assert await load_json_object_optional(_Req({"a": 1})) == {"a": 1}  # type: ignore[arg-type]
        assert await load_json_object_optional(_Req(None)) == {}  # type: ignore[arg-type]
        assert await load_json_object_optional(_Req(ValueError("bad"))) == {}  # type: ignore[arg-type]
        with pytest.raises(ApiError) as ei:
            await load_json_object_optional(_Req([1, 2]))  # type: ignore[arg-type]
        assert ei.value.message == "Invalid JSON body"

    asyncio.run(_run())


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
