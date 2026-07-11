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


async def load_json_object_optional(request: Request) -> dict[str, Any]:
    """
    Parse optional JSON object body.

    Empty/missing/invalid-parse → {}; non-object JSON → BAD_REQUEST.
    Used by admin endpoints that accept empty body with defaults.
    """
    try:
        data = await request.json()
    except Exception:
        return {}
    if data is None:
        return {}
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


def parse_required_str(
    value: Any,
    *,
    field: str,
    max_len: int | None = None,
    missing_message: str | None = None,
    invalid_message: str | None = None,
) -> str:
    """Strip required string; empty → missing; over-length → invalid."""
    text = str(value or "").strip()
    if not text:
        raise ApiError(
            code=ErrorCode.BAD_REQUEST,
            message=missing_message or f"Missing {field}",
            status_code=400,
        )
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


def parse_int_in_range(
    value: Any,
    *,
    field: str,
    min_value: int | None = None,
    max_value: int | None = None,
    invalid_message: str | None = None,
) -> int:
    """Parse an int and enforce optional inclusive bounds (message stable for field)."""
    invalid = invalid_message or f"Unsupported {field}"
    try:
        i = int(value)
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400) from exc
    if min_value is not None and i < min_value:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)
    if max_value is not None and i > max_value:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)
    return i


def parse_float_in_range(
    value: Any,
    *,
    field: str,
    min_value: float | None = None,
    max_value: float | None = None,
    invalid_message: str | None = None,
) -> float:
    """Parse a float and enforce optional inclusive bounds (message stable for field)."""
    invalid = invalid_message or f"Unsupported {field}"
    try:
        f = float(value)
    except Exception as exc:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400) from exc
    if min_value is not None and f < min_value:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)
    if max_value is not None and f > max_value:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)
    return f


def coerce_float(value: Any) -> float | None:
    """
    Lenient float coercion used by soft settings normalizers.

    None / bool / unparseable → None (bool is rejected so True/False never become 1.0/0.0).
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except Exception:
        return None


def parse_int_clamped(
    value: Any,
    *,
    field: str,
    min_value: int,
    max_value: int,
    strict: bool = True,
    default: int | None = None,
    invalid_message: str | None = None,
) -> int:
    """
    Parse int and clamp to inclusive [min_value, max_value].

    - strict: unparseable → BAD_REQUEST (same message style as parse_int_in_range)
    - soft: unparseable → default (required when strict=False)
    """
    invalid = invalid_message or f"Unsupported {field}"
    try:
        n = int(value)
    except Exception as exc:
        if strict:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400) from exc
        if default is None:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400) from exc
        return int(default)
    if n < min_value:
        if strict:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)
        n = min_value
    if n > max_value:
        if strict:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)
        n = max_value
    return int(n)


def parse_float_clamped(
    value: Any,
    *,
    field: str,
    min_value: float,
    max_value: float,
    strict: bool = True,
    default: float | None = None,
    invalid_message: str | None = None,
    require_finite: bool = False,
) -> float:
    """
    Soft/strict float parse with clamp.

    Uses coerce_float semantics (bool rejected). On invalid:
    - strict → BAD_REQUEST
    - soft → default (required when strict=False)
    On success → clamp to [min_value, max_value].
    """
    invalid = invalid_message or f"Unsupported {field}"
    v = coerce_float(value)
    if v is None or (require_finite and not _is_finite(v)):
        if strict:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)
        if default is None:
            raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)
        return float(default)
    return float(max(float(min_value), min(float(v), float(max_value))))


def _is_finite(value: float) -> bool:
    # Local helper avoids importing math at module top for a single check.
    return value == value and value not in (float("inf"), float("-inf"))


def parse_choice(
    value: Any,
    *,
    field: str,
    choices: set[str] | frozenset[str] | tuple[str, ...],
    default: str | None = None,
    invalid_message: str | None = None,
    lower: bool = True,
) -> str:
    """
    Parse a string choice from a fixed set.

    Empty/missing uses default when provided; otherwise raises with invalid_message.
    """
    invalid = invalid_message or f"Unsupported {field}"
    text = str(value or "").strip()
    if lower:
        text = text.lower()
    if not text:
        if default is not None:
            return default
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)
    if text not in choices:
        raise ApiError(code=ErrorCode.BAD_REQUEST, message=invalid, status_code=400)
    return text


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
