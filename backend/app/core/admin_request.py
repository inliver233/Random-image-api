from __future__ import annotations

from typing import Any

from fastapi import Request

from app.core.errors import ApiError, ErrorCode


async def load_json_object(request: Request) -> dict[str, Any]:
    """Parse request body as a JSON object; raise BAD_REQUEST otherwise."""
    try:
        data = await request.json()
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400) from exc
    if not isinstance(data, dict):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message="Invalid JSON body", status_code=400)
    return data


def parse_bool(value: Any, *, default: bool) -> bool:
    """Lenient bool parse used by admin forms (missing/unknown → default)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    return default


def parse_bool_optional(value: Any) -> bool | None:
    """Strict-ish bool parse: returns None when value is missing/unrecognized."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y", "on"}:
            return True
        if v in {"0", "false", "no", "n", "off"}:
            return False
    return None
