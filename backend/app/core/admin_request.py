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


def parse_optional_str(
    value: Any,
    *,
    max_len: int | None = None,
    field: str = "value",
    invalid_message: str | None = None,
) -> str | None:
    """Strip optional string; empty → None. Rejects over-length when max_len set."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if max_len is not None and len(text) > max_len:
        raise ApiError(
            code=ErrorCode.BAD_REQUEST,
            message=invalid_message or f"Unsupported {field}",
            status_code=400,
        )
    return text


def parse_positive_int(
    value: Any,
    *,
    field: str,
    invalid_message: str | None = None,
) -> int:
    """Parse a single positive int; raises BAD_REQUEST on missing/invalid/non-positive."""
    invalid = invalid_message or f"Invalid {field}"
    try:
        i = int(value)
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400) from exc
    if i <= 0:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)
    return i


def parse_positive_int_list(
    raw: Any,
    *,
    field: str,
    allow_empty: bool = False,
    max_items: int | None = None,
    invalid_message: str | None = None,
    empty_message: str | None = None,
    too_many_message: str | None = None,
) -> list[int]:
    """
    Parse a list of positive ints with de-dupe (first-seen order).

    Raises BAD_REQUEST with field-oriented messages (stable for existing APIs).
    """
    invalid = invalid_message or f"Unsupported {field}"
    empty = empty_message or f"Empty {field}"
    too_many = too_many_message or f"Too many {field}"

    if not isinstance(raw, list):
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)

    ids: list[int] = []
    seen: set[int] = set()
    for item in raw:
        try:
            i = int(item)
        except Exception as exc:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400) from exc
        if i <= 0 or i in seen:
            continue
        seen.add(i)
        ids.append(i)

    if not ids and not allow_empty:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=empty, status_code=400)
    if max_items is not None and len(ids) > max_items:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=too_many, status_code=400)
    return ids
