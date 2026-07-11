from __future__ import annotations

from types import SimpleNamespace

from app.core.admin_json import admin_cursor_list, admin_ok


class _Req:
    def __init__(self) -> None:
        self.state = SimpleNamespace()
        self.headers = {}


def test_admin_ok_payload() -> None:
    req = _Req()
    body = admin_ok(req, payload={"x": 1}, request_id="rid-1")
    assert body == {"ok": True, "request_id": "rid-1", "x": 1}


def test_admin_cursor_list_empty_cursor() -> None:
    req = _Req()
    body = admin_cursor_list(req, items=[{"a": 1}], next_cursor=None, request_id="r2")
    assert body["ok"] is True
    assert body["items"] == [{"a": 1}]
    assert body["next_cursor"] == ""
    assert body["request_id"] == "r2"


def test_admin_cursor_list_int_cursor() -> None:
    req = _Req()
    body = admin_cursor_list(req, items=[], next_cursor=42, request_id="r3")
    assert body["next_cursor"] == "42"
