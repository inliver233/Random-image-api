from __future__ import annotations

import pytest

from app.core.errors import ApiError
from app.core.public_search_query import parse_public_search_query


def test_parse_public_search_query_int_cursor_happy() -> None:
    p = parse_public_search_query(q="  alice  ", limit=50, cursor="12", cursor_kind="int")
    assert p.q == "alice"
    assert p.limit == 50
    assert p.cursor_i == 12
    assert p.cursor_s is None


def test_parse_public_search_query_str_cursor_happy() -> None:
    p = parse_public_search_query(q=None, limit=10, cursor="foo", cursor_kind="str")
    assert p.q is None
    assert p.cursor_i is None
    assert p.cursor_s == "foo"


def test_parse_public_search_query_rejects_bad_limit() -> None:
    with pytest.raises(ApiError) as ei:
        parse_public_search_query(q=None, limit=0, cursor=None, cursor_kind="int")
    assert ei.value.status_code == 400
    assert "limit" in ei.value.message.lower()


def test_parse_public_search_query_rejects_long_q() -> None:
    with pytest.raises(ApiError) as ei:
        parse_public_search_query(q="x" * 201, limit=10, cursor=None, cursor_kind="str")
    assert ei.value.status_code == 400


def test_parse_public_search_query_rejects_bad_int_cursor() -> None:
    with pytest.raises(ApiError) as ei:
        parse_public_search_query(q=None, limit=10, cursor="abc", cursor_kind="int")
    assert ei.value.status_code == 400
