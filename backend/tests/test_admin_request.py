from __future__ import annotations

import pytest

from app.core.admin_request import parse_bool, parse_bool_optional
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
