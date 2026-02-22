from __future__ import annotations

import secrets
from typing import Any, Mapping

REQUEST_ID_HEADER = "X-Request-Id"


def new_request_id() -> str:
    return "req_" + secrets.token_hex(8)


def get_request_id_from_headers(headers: Mapping[str, str] | None) -> str | None:
    if not headers:
        return None
    value = headers.get(REQUEST_ID_HEADER) or headers.get(REQUEST_ID_HEADER.lower())
    if not value:
        return None
    value = value.strip()
    return value if value else None


def set_request_id_on_state(request: Any, request_id: str) -> None:
    state = getattr(request, "state", None)
    if state is None:
        class _State:
            pass

        state = _State()
        setattr(request, "state", state)
    setattr(state, "request_id", request_id)


def set_request_id_header(response: Any, request_id: str) -> None:
    headers = getattr(response, "headers", None)
    if headers is None:
        setattr(response, "headers", {})
        headers = response.headers
    headers[REQUEST_ID_HEADER] = request_id


def get_or_create_request_id(request: Any) -> str:
    state = getattr(request, "state", None)
    if state is not None:
        rid = getattr(state, "request_id", None)
        if rid:
            return str(rid)
    headers = getattr(request, "headers", None)
    rid = get_request_id_from_headers(headers)
    return rid or new_request_id()


def build_request_id_middleware() -> Any | None:
    try:
        from starlette.middleware.base import BaseHTTPMiddleware
    except Exception:
        return None

    class RequestIdMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
            rid = get_or_create_request_id(request)
            set_request_id_on_state(request, rid)
            response = await call_next(request)
            set_request_id_header(response, rid)
            return response

    return RequestIdMiddleware
