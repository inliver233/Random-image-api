from __future__ import annotations

import pytest

from app.core.admin_cursor_query import parse_admin_int_cursor
from app.core.errors import ApiError


def test_parse_admin_int_cursor_happy() -> None:
    p = parse_admin_int_cursor(limit=50, cursor="99", limit_max=200)
    assert p.limit == 50
    assert p.cursor_i == 99


def test_parse_admin_int_cursor_empty() -> None:
    p = parse_admin_int_cursor(limit=20, cursor=None)
    assert p.cursor_i is None


def test_parse_admin_int_cursor_rejects_limit() -> None:
    with pytest.raises(ApiError) as ei:
        parse_admin_int_cursor(limit=0, cursor=None)
    assert ei.value.status_code == 400


def test_parse_admin_int_cursor_rejects_cursor() -> None:
    with pytest.raises(ApiError) as ei:
        parse_admin_int_cursor(limit=10, cursor="0")
    assert ei.value.status_code == 400
