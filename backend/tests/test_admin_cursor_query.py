from __future__ import annotations

import pytest

from app.core.admin_cursor_query import parse_admin_int_cursor, slice_id_cursor_page
from app.core.errors import ApiError


class _Row:
    def __init__(self, id: int) -> None:
        self.id = id


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


def test_slice_id_cursor_page_has_more() -> None:
    rows = [_Row(5), _Row(4), _Row(3)]
    page, next_cursor = slice_id_cursor_page(rows, 2)
    assert [r.id for r in page] == [5, 4]
    assert next_cursor == 4


def test_slice_id_cursor_page_no_more() -> None:
    rows = [_Row(2), _Row(1)]
    page, next_cursor = slice_id_cursor_page(rows, 2)
    assert [r.id for r in page] == [2, 1]
    assert next_cursor is None
