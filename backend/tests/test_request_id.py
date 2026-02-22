from __future__ import annotations

from types import SimpleNamespace

from app.core.request_id import (
    REQUEST_ID_HEADER,
    get_or_create_request_id,
    get_request_id_from_headers,
    new_request_id,
    set_request_id_header,
    set_request_id_on_state,
)


def test_new_request_id_format() -> None:
    rid = new_request_id()
    assert rid.startswith("req_")
    assert len(rid) > 8


def test_get_request_id_from_headers() -> None:
    assert get_request_id_from_headers({REQUEST_ID_HEADER: " req_1 "}) == "req_1"
    assert get_request_id_from_headers({}) is None


def test_get_or_create_request_id_uses_header() -> None:
    req = SimpleNamespace(headers={REQUEST_ID_HEADER: "req_x"})
    assert get_or_create_request_id(req) == "req_x"


def test_get_or_create_request_id_uses_state_when_present() -> None:
    req = SimpleNamespace(headers={}, state=SimpleNamespace(request_id="req_state"))
    assert get_or_create_request_id(req) == "req_state"


def test_set_request_id_on_state_and_header() -> None:
    req = SimpleNamespace(headers={})
    res = SimpleNamespace(headers={})
    set_request_id_on_state(req, "req_abc")
    set_request_id_header(res, "req_abc")
    assert req.state.request_id == "req_abc"
    assert res.headers[REQUEST_ID_HEADER] == "req_abc"
